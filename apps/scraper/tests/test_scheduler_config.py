def test_daily_refresh_runs_at_5am_beijing_time():
    from app.main import (
        DAILY_REFRESH_HOUR,
        DAILY_REFRESH_MINUTE,
        DAILY_REFRESH_TIMEZONE,
        build_daily_refresh_trigger,
    )

    trigger = build_daily_refresh_trigger()
    fields = {field.name: str(field) for field in trigger.fields}

    assert DAILY_REFRESH_TIMEZONE == "Asia/Shanghai"
    assert DAILY_REFRESH_HOUR == 5
    assert DAILY_REFRESH_MINUTE == 0
    assert str(trigger.timezone) == "Asia/Shanghai"
    assert fields["hour"] == "5"
    assert fields["minute"] == "0"
