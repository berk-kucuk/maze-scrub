import os

SUPPORTED = {".jpg": "image/jpeg", ".pdf": "application/pdf", ".odt": "application/vnd.oasis.opendocument.text"}


class FakeParser:
    def __init__(self, filename):
        self.filename = filename
        base, ext = os.path.splitext(filename)
        self.output_filename = f"{base}.cleaned{ext}"
        self.lightweight_cleaning = False
        self.sandbox = True
        with open(filename, encoding="utf-8") as f:
            self.lines = f.read().splitlines()

    def get_meta(self):
        meta = {}
        for line in self.lines:
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if "/" in key:
                member, key = key.split("/", 1)
                meta.setdefault(member, {})[key] = value
            else:
                meta[key] = value
        return meta

    def remove_all(self):
        if any("FAIL" in l for l in self.lines):
            return False
        keep = [l for l in self.lines if "=" not in l
                or (self.lightweight_cleaning and l.startswith("Software="))]
        with open(self.output_filename, "w", encoding="utf-8") as f:
            f.write("\n".join(keep) + "\n")
        return True


def get_parser(filename):
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".bad":
        raise ValueError("corrupt file")
    if ext not in SUPPORTED:
        return None, "application/octet-stream"
    return FakeParser(filename), SUPPORTED[ext]
