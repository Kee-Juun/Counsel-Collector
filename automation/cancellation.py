import time


class RunCancelled(Exception):
    pass


def is_cancelled(stop_event=None):
    return bool(stop_event and stop_event.is_set())


def raise_if_cancelled(stop_event=None):
    if is_cancelled(stop_event):
        raise RunCancelled("Collection cancelled by user.")


def sleep_with_cancel(seconds, stop_event=None):
    end_time = time.time() + seconds
    while time.time() < end_time:
        raise_if_cancelled(stop_event)
        time.sleep(min(0.2, end_time - time.time()))
