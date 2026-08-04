import threading

# Serializes heavy GeeLark operations (scheduler jobs vs manual
# publish / create-profiles) so they never run concurrently against
# the same phones/accounts. Single-process only (matches the
# --workers 1 constraint).
OPERATION_LOCK = threading.Lock()
