import pytest

from tiktok_extractor.url_resolver import classify_url


class TestClassifyUrl:
    def test_canonical_video_url(self):
        p = classify_url("https://www.tiktok.com/@example_user/video/7300000000000000001")
        assert p.kind == "canonical"
        assert p.post_type == "video"
        assert p.author == "example_user"
        assert p.post_id == "7300000000000000001"

    def test_canonical_photo_url(self):
        p = classify_url("https://www.tiktok.com/@photo_user/photo/7300000000000000004")
        assert p.kind == "canonical"
        assert p.post_type == "photo"

    def test_canonical_url_with_query_params(self):
        p = classify_url(
            "https://www.tiktok.com/@example_user/video/7300000000000000001"
            "?_t=8e11Qw3KoOg&_r=1"
        )
        assert p.kind == "canonical"
        assert p.post_id == "7300000000000000001"

    def test_vt_short_link(self):
        p = classify_url("https://vt.tiktok.com/ZSLP7GCYQ/")
        assert p.kind == "short_link"
        assert p.post_type is None

    def test_vm_short_link(self):
        p = classify_url("https://vm.tiktok.com/ZSk1wCAxQ/")
        assert p.kind == "short_link"

    def test_unrecognized_url_raises(self):
        with pytest.raises(ValueError):
            classify_url("https://example.com/not-a-tiktok-url")

    def test_username_with_dots_and_underscores(self):
        p = classify_url("https://www.tiktok.com/@a.b_c-d/video/123456")
        assert p.author == "a.b_c-d"
