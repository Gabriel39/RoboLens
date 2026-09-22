"""Export both saved notebook versions to HTML without executing any cells."""
from __future__ import annotations

import base64
from html import escape
from pathlib import Path
import re
from xml.etree import ElementTree

import nbformat
from nbconvert import HTMLExporter


def accessible_diagrams(page: str) -> str:
    """Use each embedded SVG title as the HTML image's accessible description."""
    def replace(match):
        tag = match.group(0)
        data = re.search(r'src="data:image/svg\+xml;base64,([^"]+)"', tag)
        svg = ElementTree.fromstring(base64.b64decode(data.group(1)))
        title = svg.find("{http://www.w3.org/2000/svg}title")
        if title is None or not title.text:
            return tag
        alt = 'alt="' + escape(title.text, quote=True) + '"'
        if re.search(r'\balt="[^"]*"', tag):
            return re.sub(r'\balt="[^"]*"', lambda _: alt, tag)
        return tag.replace("<img ", "<img " + alt + " ", 1)
    return re.sub(r'<img\b[^>]*src="data:image/svg\+xml;base64,[^"]+"[^>]*>', replace, page)


def main():
    directory = Path(__file__).resolve().parent
    for suffix, language in (("", "zh-CN"), (".en", "en")):
        notebook = nbformat.read(directory / f"droid100_end_to_end{suffix}.ipynb", as_version=4)
        nbformat.validate(notebook)
        exporter = HTMLExporter(template_name="lab", exclude_input=True)
        page, _ = exporter.from_notebook_node(notebook)
        page = re.sub(r'<html\b[^>]*>', f'<html lang="{language}">', page, count=1)
        target = directory / f"droid100_preview{suffix}.html"
        target.write_text(accessible_diagrams(page))
        print(target)


if __name__ == "__main__":
    main()
