"""The site's Discord link previews (component embeds) and icon set.

Previews: each page's pre-built <script id="discord:component-embed"> block, written by
scripts/build-discord-embeds.py, is checked against Discord's rules (one Container at the root,
link buttons only, at most 40 components, 3,000 bytes at most) and against the page itself. The
404 page has none.
Icons: favicon.ico, apple-touch-icon.png and site.webmanifest at the site root, and the same four
tags in every page's <head>.
https://docs.discord.com/developers/link-previews/component-embeds

Run with: bash tests/site.sh
"""
import importlib.util
import json
import re
import struct
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True  # no __pycache__ in the site (GitHub Pages publishes the whole folder)
ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location("build_discord_embeds", ROOT / "scripts" / "build-discord-embeds.py")
embeds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(embeds)

OPEN = '<script id="discord:component-embed" type="application/json">'
CLOSE = "</script>"
TAG = re.compile(re.escape(OPEN) + r"(.*?)</script>", re.S)
BUTTON_KEYS = {"id", "type", "url", "style", "label", "emoji", "disabled"}
PNG = b"\x89PNG\r\n\x1a\n"
ICON_TAGS = [
    '<link rel="icon" href="/favicon.ico" sizes="48x48">',
    '<link rel="icon" type="image/png" sizes="512x512" href="/assets/img/icon-512.png">',
    '<link rel="apple-touch-icon" href="/apple-touch-icon.png">',
    '<link rel="manifest" href="/site.webmanifest">',
]
ICON_LINK = re.compile(r'<link rel="(?:icon|apple-touch-icon|manifest)"[^>]*>')


def html_pages() -> list:
    """Every HTML page in the site that has a <head>."""
    return sorted(p for p in ROOT.rglob("*.html")
                  if ".git" not in p.parts and "node_modules" not in p.parts
                  and "<head" in p.read_text(encoding="utf-8"))


def flatten(component: dict) -> list:
    """Every component in the tree, the root included."""
    found = [component]
    children = list(component.get("components", [])) + ([component["accessory"]] if "accessory" in component else [])
    for child in children:
        found += flatten(child)
    return found


def headline(payload: dict) -> str:
    """The text of the first section, where every preview puts its title and description."""
    return payload["component"]["components"][0]["components"][0]["content"]


def decode(tag: str) -> dict:
    return json.loads(tag[len(OPEN):-len(CLOSE)])


def png_info(data: bytes) -> tuple:
    """(width, height, colour type) from a PNG's IHDR; colour types 4 and 6 have alpha."""
    assert data[:8] == PNG, "not a PNG"
    width, height, _depth, colour = struct.unpack(">IIBB", data[16:26])
    return width, height, colour


def site_file(url: str) -> Path:
    return ROOT / url.lstrip("/")


class DiscordEmbedTest(unittest.TestCase):
    def assert_valid(self, tag: str, label: str = "") -> dict:
        self.assertTrue(tag.startswith(OPEN) and tag.endswith(CLOSE), label + ": preview left out or malformed")
        raw = tag[len(OPEN):-len(CLOSE)]
        self.assertLessEqual(len(raw.encode("utf-8")), 3000, label + ": over 3,000 bytes")
        self.assertNotIn("</", raw, label + ": could close the <script> early")
        self.assertNotIn("$1", raw, label + ": an unfilled regex group")
        payload = json.loads(raw)
        self.assertEqual(["component"], list(payload), label)
        root = payload["component"]
        self.assertEqual(17, root["type"], label + ": the root is one Container")
        self.assertEqual(int(embeds.SITE_COLOUR.lstrip("#"), 16), root["accent_color"], label)
        everything = flatten(root)
        self.assertLessEqual(len(everything), 40, label)
        for c in everything:
            self.assertIn(c["type"], (1, 2, 9, 10, 11, 12, 14, 17), label)
            if c["type"] == 17:
                self.assertIs(root, c, label + ": no nested Containers")
            if c["type"] == 2:
                self.assertEqual(5, c["style"], label + ": link buttons only")
                self.assertLessEqual(set(c), BUTTON_KEYS, label)
                self.assertRegex(c["url"], r"^https?://", label)
        return payload

    def test_every_committed_preview_is_valid_and_current(self):
        listing = embeds.pages()
        self.assertGreaterEqual(len(listing), 9, "home, changelog, legal hub and its six pages at least")
        sources = {src for _path, src, _label in listing}
        for path, src, label in listing:
            page = (ROOT / src).read_text(encoding="utf-8")
            tags = [m.group(0) for m in TAG.finditer(page)]
            self.assertEqual(1, len(tags), src + ": one preview")
            self.assertIn(tags[0], page[:page.index("</head>")], src + ": the preview belongs in <head>")
            payload = self.assert_valid(tags[0], src)
            self.assertEqual(tags[0], embeds.embed_for(path, label, page),
                             src + ": out of date, run python scripts/build-discord-embeds.py")
            if path != "/":
                self.assertIn("## [" + embeds.discord_md(label) + "](" + embeds.BASE_URL + path + ")", headline(payload), src)
        for page in html_pages():
            if page.relative_to(ROOT).as_posix() not in sources:
                self.assertNotIn("discord:component-embed", page.read_text(encoding="utf-8"),
                                 page.name + ": no preview on error pages")

    def test_404_has_no_preview(self):
        self.assertNotIn("discord:component-embed", (ROOT / "404.html").read_text(encoding="utf-8"))
        self.assertNotIn("404.html", {src for _path, src, _label in embeds.pages()})

    def test_home_preview_says_what_the_page_is(self):
        page = (ROOT / "index.html").read_text(encoding="utf-8")
        payload = self.assert_valid(embeds.embed_for("/", "Home", page), "home")
        self.assertIn("**Coming soon**", headline(payload))
        self.assertIn(embeds.SITE_NAME, headline(payload))
        self.assertEqual(embeds.ICON_URL, payload["component"]["components"][0]["accessory"]["media"]["url"])
        labels = [c["label"] for c in flatten(payload["component"]) if c["type"] == 2]
        self.assertTrue(1 <= len(labels) <= 5, labels)

    def test_legal_hub_preview_links_every_legal_page(self):
        page = (ROOT / "legal.html").read_text(encoding="utf-8")
        payload = self.assert_valid(embeds.embed_for("/legal", "Boring Legal Stuff", page), "legal")
        text = json.dumps(payload, ensure_ascii=False)
        for slug in ("privacy", "terms", "cookies", "imprint", "disclaimer", "opt-out"):
            self.assertIn(embeds.BASE_URL + "/legal/" + slug + ")", text)

    def test_markdown_in_data_is_escaped(self):
        # One backslash before each special character, never a literal "$1"
        self.assertEqual(r"\*\*bold\*\* a\_b", embeds.discord_md("**bold** a_b"))
        self.assertEqual(r"\*\*bold\*\* \[x\](y) a\_b \~\~s\~\~ \`c\` \|\|p\|\| \\",
                         embeds.discord_md("**bold** [x](y) a_b ~~s~~ `c` ||p|| \\"))
        self.assertEqual("\\# heading\n\\- list\n\\> quote\nmid # - > stays",
                         embeds.discord_md("# heading\n- list\n> quote\nmid # - > stays"))
        self.assertNotIn("$1", embeds.discord_md("**bold** a_b"))

    def test_escaped_data_reaches_the_preview_intact(self):
        payload = self.assert_valid(embeds.page_embed("A_b *c*", "# not a heading", "https://example.com/x"), "escaped")
        self.assertIn("## [A\\_b \\*c\\*](https://example.com/x)\n\\# not a heading", headline(payload))
        self.assertNotIn("$1", headline(payload))

    def test_script_cant_be_closed_early(self):
        tag = embeds.page_embed("</script><b>x", "d", "https://example.com/x")
        self.assertEqual(1, tag.count(CLOSE), "only the tag's own closing")
        # JSON's <\/ decodes back to </, so Discord still shows the title as written
        self.assertIn("## [</script><b>x](", headline(decode(tag)))

    def test_over_the_limit_falls_back_to_the_page_title_and_description(self):
        tag = embeds.page_embed("T", "Long description. " * 300, "https://example.com/x")
        self.assertTrue(tag.startswith("<!-- discord:component-embed left out"), tag[:80])


class IconTest(unittest.TestCase):
    def test_favicon_ico_holds_16_32_and_48px_pngs(self):
        data = (ROOT / "favicon.ico").read_bytes()
        _reserved, kind, count = struct.unpack("<HHH", data[:6])
        self.assertEqual(1, kind)
        sizes = []
        for i in range(count):
            w, h, _c, _r, _p, _b, size, offset = struct.unpack("<BBBBHHII", data[6 + 16 * i:22 + 16 * i])
            self.assertEqual(PNG, data[offset:offset + 8], "%dpx entry is a PNG" % w)
            self.assertEqual((w, h), png_info(data[offset:offset + size])[:2])
            sizes.append(w)
        self.assertEqual([16, 32, 48], sorted(sizes))

    def test_apple_touch_icon_is_180px_and_opaque(self):
        w, h, colour = png_info((ROOT / "apple-touch-icon.png").read_bytes())
        self.assertEqual((180, 180), (w, h))
        self.assertIn(colour, (0, 2), "opaque: iOS fills transparency with black")

    def test_manifest(self):
        manifest = json.loads((ROOT / "site.webmanifest").read_text(encoding="utf-8"))
        for key in ("name", "short_name", "theme_color", "background_color"):
            self.assertTrue(manifest.get(key), key)
        self.assertEqual("/", manifest["start_url"])
        self.assertEqual("browser", manifest["display"])
        found = {}
        for icon in manifest["icons"]:
            w, h, colour = png_info(site_file(icon["src"]).read_bytes())
            self.assertEqual(icon["sizes"], "%dx%d" % (w, h), icon["src"])
            found[(icon["sizes"], icon.get("purpose", "any"))] = colour
        self.assertIn(("192x192", "any"), found)
        self.assertIn(("512x512", "any"), found)
        self.assertIn(found.get(("512x512", "maskable")), (0, 2), "a 512px maskable icon on an opaque background")

    def test_every_page_lists_the_same_icons(self):
        pages = html_pages()
        self.assertIn(ROOT / "404.html", pages)
        for page in pages:
            text = page.read_text(encoding="utf-8")
            head = text[:text.index("</head>")]
            self.assertEqual(ICON_TAGS, ICON_LINK.findall(head), page.relative_to(ROOT).as_posix())
        for url in ("/favicon.ico", "/assets/img/icon-512.png", "/apple-touch-icon.png", "/site.webmanifest"):
            self.assertTrue(site_file(url).is_file(), url)


if __name__ == "__main__":
    unittest.main()
