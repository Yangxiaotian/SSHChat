import unittest

import server


class RoomGameCatalogCompatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = {
            room: set(enabled)
            for room, enabled in server.room_enabled_games.items()
        }
        server.room_enabled_games.clear()

    def tearDown(self) -> None:
        server.room_enabled_games.clear()
        server.room_enabled_games.update(self.previous)

    def test_legacy_room_config_enables_games_added_after_persistence(self) -> None:
        server._apply_session_payload_locked(
            {
                "room_enabled_games": {
                    "default": ["chess", "gomoku"],
                },
            }
        )

        enabled = server._enabled_games_for_room_locked("default")
        for game_name in server.ROOM_GAME_CATALOG_MIGRATION_IDS:
            self.assertIn(game_name, enabled)
        self.assertEqual(enabled & {"chess", "gomoku"}, {"chess", "gomoku"})

    def test_current_room_config_preserves_explicitly_disabled_games(self) -> None:
        server._apply_session_payload_locked(
            {
                "room_enabled_games_version": server.ROOM_GAME_CATALOG_VERSION,
                "room_enabled_games": {
                    "default": ["chess", "gomoku"],
                },
            }
        )

        enabled = server._enabled_games_for_room_locked("default")
        for game_name in server.ROOM_GAME_CATALOG_MIGRATION_IDS:
            self.assertNotIn(game_name, enabled)

    def test_new_room_starts_with_every_registered_game(self) -> None:
        enabled = server._enabled_games_for_room_locked("new-room")
        self.assertEqual(enabled, set(server.games.GAMES))

    def test_saved_catalog_contains_schema_version(self) -> None:
        server.room_enabled_games["default"] = {"chess"}
        payload = server._build_session_payload_locked()
        self.assertEqual(
            payload["room_enabled_games_version"],
            server.ROOM_GAME_CATALOG_VERSION,
        )


if __name__ == "__main__":
    unittest.main()
