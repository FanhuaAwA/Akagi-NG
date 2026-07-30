__all__ = ["Controller", "StateTracker"]


def __getattr__(name: str) -> type:
    """Avoid loading native libriichi modules for independent helpers such as OT3."""
    if name == "Controller":
        from akagi_ng.mjai_bot.controller import Controller

        return Controller
    if name == "StateTracker":
        from akagi_ng.mjai_bot.tracker import StateTracker

        return StateTracker
    raise AttributeError(name)
