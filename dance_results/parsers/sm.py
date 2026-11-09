from ..models import ParsedFile
from .common import read_msd_titles


class SMParser:
    def parse(self, text: str, result: ParsedFile) -> None:
        read_msd_titles(text, result, unescape=True)
