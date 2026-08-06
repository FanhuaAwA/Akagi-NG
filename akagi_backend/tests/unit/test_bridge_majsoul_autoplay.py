from akagi_ng.bridge.majsoul import MajsoulBridge
from akagi_ng.schema.constants import MahjongConstants


def test_action_prototype_captures_self_operation_list_for_autoplay():
    bridge = MajsoulBridge()
    bridge.seat = 1

    message = {
        "method": ".lq.ActionPrototype",
        "type": 1,
        "data": {
            "step": 18,
            "name": "ActionDealTile",
            "data": {
                "seat": 1,
                "tile": "5m",
                "leftTileCount": 60,
                "operation": {
                    "seat": 1,
                    "operationList": [
                        {"type": 7, "combination": []},
                        {"type": 9, "combination": []},
                    ],
                },
            },
        },
    }

    bridge.parse_liqi(message)

    assert bridge.latest_operation_step == 18
    assert bridge.latest_self_operation_list == [
        {"type": 7, "combination": []},
        {"type": 9, "combination": []},
    ]


def test_action_prototype_clears_self_operation_list_after_self_action_without_operation():
    bridge = MajsoulBridge()
    bridge.seat = 0
    bridge.latest_self_operation_list = [{"type": 7, "combination": []}]
    bridge.latest_operation_step = 11

    message = {
        "method": ".lq.ActionPrototype",
        "type": 1,
        "data": {
            "step": 12,
            "name": "ActionDiscardTile",
            "data": {
                "seat": 0,
                "tile": "3m",
                "isLiqi": False,
                "moqie": False,
            },
        },
    }

    bridge.parse_liqi(message)

    assert bridge.latest_self_operation_list == []
    assert bridge.latest_operation_step is None


def test_action_prototype_clears_stale_self_operation_list_after_other_player_action():
    bridge = MajsoulBridge()
    bridge.seat = 0
    bridge.latest_self_operation_list = [{"type": 0, "combination": []}, {"type": 9, "combination": []}]
    bridge.latest_operation_step = 25

    message = {
        "method": ".lq.ActionPrototype",
        "type": 1,
        "data": {
            "step": 26,
            "name": "ActionDealTile",
            "data": {
                "seat": 1,
                "tile": "5m",
                "leftTileCount": 60,
            },
        },
    }

    bridge.parse_liqi(message)

    assert bridge.latest_self_operation_list == []
    assert bridge.latest_operation_step is None


def test_action_prototype_tracks_implicit_self_discard_window_without_special_operations():
    bridge = MajsoulBridge()
    bridge.seat = 2

    bridge.parse_liqi(
        {
            "method": ".lq.ActionPrototype",
            "type": 1,
            "data": {
                "step": 31,
                "name": "ActionDealTile",
                "data": {"seat": 2, "tile": "7p", "leftTileCount": 42},
            },
        }
    )

    assert bridge.latest_self_operation_list == []
    assert bridge.latest_operation_step == 31


def test_action_prototype_tracks_dealer_opening_discard_window():
    bridge = MajsoulBridge()

    bridge._capture_self_operation_list(
        {
            "data": {
                "step": 1,
                "name": "ActionNewRound",
                "data": {"tiles": ["1m"] * MahjongConstants.TSUMO_TEHAI_SIZE},
            }
        }
    )

    assert bridge.latest_self_operation_list == []
    assert bridge.latest_operation_step == 1


def test_action_prototype_clears_stale_window_when_operations_belong_to_other_player():
    bridge = MajsoulBridge()
    bridge.seat = 0
    bridge.latest_self_operation_list = [{"type": 7, "combination": []}]
    bridge.latest_operation_step = 7

    bridge._capture_self_operation_list(
        {
            "data": {
                "step": 8,
                "name": "ActionDealTile",
                "data": {
                    "seat": 1,
                    "operation": {"seat": 1, "operationList": [{"type": 8, "combination": []}]},
                },
            }
        }
    )

    assert bridge.latest_self_operation_list == []
    assert bridge.latest_operation_step is None
