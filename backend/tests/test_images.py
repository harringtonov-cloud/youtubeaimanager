from app.services import images


def test_build_prompt_includes_scene_topic_and_style():
    p = images.build_prompt("iron filings magnet", "Magnets pull iron because of fields.", "How magnets work")
    assert "iron filings magnet" in p and "How magnets work" in p and "no text" in p
    assert len(p) <= 480


def test_looks_like_image():
    assert images._looks_like_image(b"\xff\xd8\xff" + b"0" * 9000)
    assert not images._looks_like_image(b"<html>" + b"0" * 9000)
    assert not images._looks_like_image(b"\xff\xd8\xff")  # too small


class _Resp:
    def __init__(self, status, content):
        self.status_code, self.content = status, content


def test_generate_retries_then_succeeds(monkeypatch, tmp_path):
    calls = []

    def fake_get(url, **kw):
        calls.append(kw["params"]["seed"])
        return _Resp(429, b"slow down") if len(calls) == 1 else _Resp(200, b"\xff\xd8\xff" + b"1" * 9000)

    monkeypatch.setattr(images.httpx, "get", fake_get)
    monkeypatch.setattr(images, "MIN_GAP_S", 0.0)
    out = images.generate("a cat", tmp_path / "a.jpg", seed=5)
    assert out and out.exists() and len(calls) == 2 and calls[0] != calls[1]


def test_generate_gives_up_without_raising(monkeypatch, tmp_path):
    def boom(url, **kw):
        raise TimeoutError("down")

    monkeypatch.setattr(images.httpx, "get", boom)
    monkeypatch.setattr(images, "MIN_GAP_S", 0.0)
    assert images.generate("a cat", tmp_path / "a.jpg", seed=1, attempts=2) is None
