import inspect

import notify


def test_ac_send_is_implemented() -> None:
    assert "NotImplementedError" not in inspect.getsource(notify.send)
