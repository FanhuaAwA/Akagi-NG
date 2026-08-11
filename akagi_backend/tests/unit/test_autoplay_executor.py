import pytest

from akagi_ng.autoplay.executor import WindowsInputExecutor
from akagi_ng.schema.constants import Platform


@pytest.mark.parametrize("title", ["MahjongSoul", "\u3058\u3083\u3093\u305f\u307e"])
def test_default_majsoul_window_keywords_cover_overseas_titles(title: str) -> None:
    keywords = WindowsInputExecutor()._window_keywords(Platform.MAJSOUL)

    assert any(keyword in title.lower() for keyword in keywords)
