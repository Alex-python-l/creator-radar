def _fake_client(install_ok=True):
    """Fake GeeLarkClient that records calls. Use one instance per test."""
    calls = {"create_phones": [], "get_installable_apps": [],
              "install_app": []}

    class _C:
        def create_phones(self, items):
            call_n = len(calls["create_phones"]) + 1
            calls["create_phones"].append(items)
            return {"details": [{"id": f"phone-{call_n}"}
                                  for _ in items]}

        def get_installable_apps(self, phone_id, name=None):
            calls["get_installable_apps"].append((phone_id, name))
            # Real GeeLark shape: name="TikTok" returns several apps whose
            # appName contains "TikTok" (Studio, Lite, Seller, etc.) — the
            # canonical TikTok is identified by packageName, not by name.
            # appVersionInfoList is sorted newest-first.
            return {"items": [
                {"appName": "TikTok Studio",
                 "packageName": "com.ss.android.tt.creator",
                 "appVersionInfoList": [{"id": "studio-v1"}]},
                {"appName": "TikTok",
                 "packageName": "com.zhiliaoapp.musically",
                 "appVersionInfoList": [
                     {"id": "tt-newest"},
                     {"id": "tt-older"},
                 ]},
            ]}

        def install_app(self, phone_id, app_version_id):
            calls["install_app"].append((phone_id, app_version_id))
            return {}

    return _C(), calls


def test_create_profiles_for_group_makes_one_phone_per_proxy(db):
    from app.services import geelark_service as gs
    from app.repositories import groups as gr
    gid = gr.create(db, name="batch-1")
    client, calls = _fake_client()

    log = list(gs.create_profiles_for_group(db, client, gid, [
        "socks5://u:p@1.1.1.1:1080",
        "socks5://u:p@2.2.2.2:1080",
    ]))

    # One create_phones call per proxy (sequential, matches existing pattern)
    assert len(calls["create_phones"]) == 2
    first = calls["create_phones"][0][0]
    assert first["proxyInformation"] == "socks5://u:p@1.1.1.1:1080"
    assert first["profileGroup"] == "batch-1"
    assert first["profileName"]
    # accounts rows written back with phone_id + group_id + proxy
    from app.repositories import accounts as acc_repo
    rows = acc_repo.list_by_group(db, gid)
    assert len(rows) == 2
    assert rows[0]["geelark_phone_id"] == "phone-1"
    assert rows[0]["proxy"] == "socks5://u:p@1.1.1.1:1080"
    assert rows[0]["group_id"] == gid
    # TikTok install attempted for each
    assert {c[0] for c in calls["install_app"]} == {"phone-1", "phone-2"}
    # Log mentions both
    joined = "\n".join(log)
    assert "phone-1" in joined and "phone-2" in joined


def test_create_profiles_for_group_continues_on_per_phone_failure(db):
    from app.services import geelark_service as gs
    from app.repositories import groups as gr
    gid = gr.create(db, name="b2")

    class _C:
        def __init__(self):
            self.n = 0
        def create_phones(self, items):
            self.n += 1
            if self.n == 1:
                return {"details": []}  # API returned nothing -> failure
            return {"details": [{"id": "phone-OK"}]}
        def get_installable_apps(self, phone_id, name=None):
            return {"items": [{"appName": "TikTok", "appVersionId": "v"}]}
        def install_app(self, phone_id, vid):
            return {}

    log = list(gs.create_profiles_for_group(db, _C(), gid,
        ["socks5://x:1080", "socks5://y:1080"]))
    from app.repositories import accounts as acc_repo
    rows = acc_repo.list_by_group(db, gid)
    # Only the second proxy's phone created successfully
    assert {r["geelark_phone_id"] for r in rows if r["geelark_phone_id"]} == \
        {"phone-OK"}
    assert any("FAILED" in line for line in log)


def test_create_profiles_count_none_keeps_1_to_1_behavior(db):
    """Regression guard — old callers that don't pass count still get
    one phone per proxy."""
    from app.services import geelark_service as gs
    from app.repositories import groups as gr
    gid = gr.create(db, name="rg1")
    client, calls = _fake_client()

    list(gs.create_profiles_for_group(db, client, gid,
        ["socks5://a:1080", "socks5://b:1080"]))

    assert len(calls["create_phones"]) == 2


def test_create_profiles_count_more_than_proxies_round_robins(db):
    """One proxy, five phones — proxy is reused for every phone."""
    from app.services import geelark_service as gs
    from app.repositories import groups as gr
    gid = gr.create(db, name="rg2")
    client, calls = _fake_client()

    list(gs.create_profiles_for_group(db, client, gid,
        ["socks5://only:1080"], count=5))

    assert len(calls["create_phones"]) == 5
    proxies_seen = [c[0]["proxyInformation"] for c in calls["create_phones"]]
    assert proxies_seen == ["socks5://only:1080"] * 5
    serials = [c[0]["profileName"] for c in calls["create_phones"]]
    assert serials == ["rg2-01", "rg2-02", "rg2-03", "rg2-04", "rg2-05"]


def test_create_profiles_count_with_multiple_proxies_cycles(db):
    """Two proxies, five phones — cycles 1,2,1,2,1."""
    from app.services import geelark_service as gs
    from app.repositories import groups as gr
    gid = gr.create(db, name="rg3")
    client, calls = _fake_client()

    list(gs.create_profiles_for_group(db, client, gid,
        ["socks5://p1:1080", "socks5://p2:1080"], count=5))

    proxies_seen = [c[0]["proxyInformation"] for c in calls["create_phones"]]
    assert proxies_seen == [
        "socks5://p1:1080", "socks5://p2:1080",
        "socks5://p1:1080", "socks5://p2:1080",
        "socks5://p1:1080",
    ]


def test_create_profiles_count_fewer_than_proxies_uses_prefix(db):
    """Three proxies, two phones — the third proxy is unused this round."""
    from app.services import geelark_service as gs
    from app.repositories import groups as gr
    gid = gr.create(db, name="rg4")
    client, calls = _fake_client()

    list(gs.create_profiles_for_group(db, client, gid,
        ["socks5://p1:1080", "socks5://p2:1080", "socks5://p3:1080"],
        count=2))

    proxies_seen = [c[0]["proxyInformation"] for c in calls["create_phones"]]
    assert proxies_seen == ["socks5://p1:1080", "socks5://p2:1080"]


def test_create_profiles_count_resumes_from_next_serial(db):
    """If the group already has rg5-01 and rg5-01 (2), the new run starts
    at rg5-02 — no (3) suffixes."""
    from app.services import geelark_service as gs
    from app.repositories import groups as gr
    from app.repositories import accounts as acc
    gid = gr.create(db, name="rg5")
    acc.create(db, {"profile_name": "rg5-01", "group_id": gid})
    acc.create(db, {"profile_name": "rg5-01 (2)", "group_id": gid})
    client, calls = _fake_client()

    list(gs.create_profiles_for_group(db, client, gid,
        ["socks5://p:1080"], count=3))

    serials = [c[0]["profileName"] for c in calls["create_phones"]]
    assert serials == ["rg5-02", "rg5-03", "rg5-04"]


def test_create_profiles_rejects_zero_count(db):
    from app.services import geelark_service as gs
    from app.repositories import groups as gr
    gid = gr.create(db, name="rg6")
    client, calls = _fake_client()

    log = list(gs.create_profiles_for_group(db, client, gid,
        ["socks5://p:1080"], count=0))

    assert calls["create_phones"] == []
    assert any("FAILED" in line for line in log)


def test_create_profiles_rejects_empty_proxies_with_count(db):
    from app.services import geelark_service as gs
    from app.repositories import groups as gr
    gid = gr.create(db, name="rg7")
    client, calls = _fake_client()

    log = list(gs.create_profiles_for_group(db, client, gid, [], count=2))

    assert calls["create_phones"] == []
    assert any("FAILED" in line for line in log)


# --- TikTok auto-install: must pick the canonical TikTok app and the
# newest version. Older code used `appVersionId` (a field GeeLark never
# returns) and matched on appName fuzzy — which would install TikTok
# Studio (the creator app) instead of TikTok proper.

def test_install_picks_tiktok_by_package_name_not_appname(db):
    """`"tiktok" in appName.lower()` matches TikTok Studio first. The
    canonical TikTok must be selected by packageName."""
    from app.services import geelark_service as gs
    from app.repositories import groups as gr
    gid = gr.create(db, name="ttp1")
    client, calls = _fake_client()

    list(gs.create_profiles_for_group(db, client, gid,
        ["socks5://p:1080"], count=1))

    # install_app called once with the musically (real TikTok) version,
    # not the Studio one.
    assert len(calls["install_app"]) == 1
    phone_id, version_id = calls["install_app"][0]
    assert version_id == "tt-newest"  # newest from appVersionInfoList


def test_install_uses_first_version_in_appVersionInfoList(db):
    """GeeLark sorts appVersionInfoList newest-first; we install [0]."""
    from app.services import geelark_service as gs
    from app.repositories import groups as gr
    gid = gr.create(db, name="ttp2")
    client, calls = _fake_client()

    list(gs.create_profiles_for_group(db, client, gid,
        ["socks5://p:1080"], count=1))

    _, version_id = calls["install_app"][0]
    assert version_id == "tt-newest"  # not "tt-older"


def test_install_warns_when_tiktok_package_missing(db):
    """If GeeLark returns no com.zhiliaoapp.musically entry, log a warning
    and continue — don't crash the wizard."""
    from app.services import geelark_service as gs
    from app.repositories import groups as gr
    gid = gr.create(db, name="ttp3")

    class _C:
        def create_phones(self, items):
            return {"details": [{"id": "ph-1"}]}
        def get_installable_apps(self, phone_id, name=None):
            return {"items": [
                {"appName": "TikTok Studio",
                 "packageName": "com.ss.android.tt.creator",
                 "appVersionInfoList": [{"id": "studio-only"}]},
            ]}
        def install_app(self, phone_id, version_id):
            raise AssertionError("install_app should not be called when "
                                 "the real TikTok package is missing")

    log = list(gs.create_profiles_for_group(db, _C(), gid,
        ["socks5://p:1080"], count=1))
    assert any("WARNING" in line and "TikTok" in line for line in log)
    # The phone itself was created — the warning is non-fatal.
    from app.repositories import accounts as acc_repo
    rows = acc_repo.list_by_group(db, gid)
    assert rows[0]["geelark_phone_id"] == "ph-1"


def test_install_warns_when_appVersionInfoList_empty(db):
    """Defensive — musically present but with zero versions."""
    from app.services import geelark_service as gs
    from app.repositories import groups as gr
    gid = gr.create(db, name="ttp4")

    class _C:
        def create_phones(self, items):
            return {"details": [{"id": "ph-1"}]}
        def get_installable_apps(self, phone_id, name=None):
            return {"items": [
                {"appName": "TikTok",
                 "packageName": "com.zhiliaoapp.musically",
                 "appVersionInfoList": []},
            ]}
        def install_app(self, phone_id, version_id):
            raise AssertionError("install_app should not be called when "
                                 "no versions are available")

    log = list(gs.create_profiles_for_group(db, _C(), gid,
        ["socks5://p:1080"], count=1))
    assert any("WARNING" in line and "TikTok" in line for line in log)
