from linguafloat.capture import shortcut_modifiers_pressed


def test_detects_pressed_shortcut_modifier():
    assert shortcut_modifiers_pressed(
        lambda key: 0x8000 if key == 0x12 else 0
    )


def test_detects_released_shortcut_modifiers():
    assert not shortcut_modifiers_pressed(lambda key: 0)
