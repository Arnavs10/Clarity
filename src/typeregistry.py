"""
Loads document type profiles from config/types/*.yaml

This is the piece that keeps a new document type a config change instead of a
code change. Nothing downstream should ever hardcode "rental" or "employment".
Ask the registry.
"""

from pathlib import Path
import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config" / "types"

REQUIRED_KEYS = ("slug", "label", "jurisdiction", "corpus", "taxonomy", "severity_scale")


class TypeRegistry:
    def __init__(self, as_dir=None):
        self.dir = Path(as_dir) if as_dir else CONFIG_DIR
        self._cache = {}
        self._load_all()

    def _load_all(self):
        if not self.dir.exists():
            raise FileNotFoundError(f"no type config dir at {self.dir}")

        for a_path in sorted(self.dir.glob("*.yaml")):
            with open(a_path, "r", encoding="utf-8") as f:
                as_profile = yaml.safe_load(f)

            missing = [k for k in REQUIRED_KEYS if k not in as_profile]
            if missing:
                raise ValueError(f"{a_path.name} is missing keys: {missing}")

            slug = as_profile["slug"]
            if slug != a_path.stem:
                raise ValueError(f"{a_path.name} declares slug '{slug}', filename says '{a_path.stem}'")

            self._cache[slug] = as_profile

    def slugs(self):
        return sorted(self._cache.keys())

    def get(self, slug):
        if slug not in self._cache:
            raise KeyError(f"unknown doc type '{slug}'. known: {self.slugs()}")
        return self._cache[slug]

    def categories(self, slug):
        return [c["id"] for c in self.get(slug)["taxonomy"]]

    def category_desc(self, slug, cat_id):
        for c in self.get(slug)["taxonomy"]:
            if c["id"] == cat_id:
                return c.get("desc", "")
        return ""

    def watch_notes(self, slug):
        """category id -> what to look out for. Feeds the labeling UI and later the prompts."""
        out = {}
        for c in self.get(slug)["taxonomy"]:
            if c.get("watch"):
                out[c["id"]] = c["watch"]
        return out

    def anchors(self, slug):
        return self.get(slug).get("anchors", [])


if __name__ == "__main__":
    as_reg = TypeRegistry()
    print("loaded types:", as_reg.slugs())
    for s in as_reg.slugs():
        cats = as_reg.categories(s)
        print(f"\n{s}: {len(cats)} categories")
        for c in cats:
            print("  -", c)
