import base64

from scripts import embed_icons


def test_template_is_in_sync_with_icons():
    # テンプレートに入っているロゴが template/icons/ と同じなら、埋め込み直しても変わらない
    template = embed_icons.TEMPLATE.read_text(encoding="utf-8")
    assert embed_icons.embed(template, embed_icons.to_css_rules(embed_icons.ICONS)) == template


def test_embed_replaces_old_rules_and_keeps_the_rest(tmp_path):
    (tmp_path / "acme.png").write_bytes(b"new")
    template = (
        "a{}\n.av.org[data-co]{font-size:0}\n"
        '.av.org[data-co="old"]{background-image:url("data:image/png;base64,b2xk")}\n'
        ".after{}"
    )
    out = embed_icons.embed(template, embed_icons.to_css_rules(tmp_path))
    assert 'data-co="old"' not in out
    assert base64.b64encode(b"new").decode() in out
    assert out.startswith("a{}\n.av.org[data-co]{font-size:0}\n")
    assert out.endswith("\n.after{}")
