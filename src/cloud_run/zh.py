"""Text people read is Traditional Chinese (Hong Kong). Speech recognition and models sometimes write
Simplified Chinese ("你听得懂广东话"), so transcripts and replies pass through here ("你聽得懂廣東話").
Only Chinese characters change; English, code and links are left as they are."""

from opencc import OpenCC

_s2hk = OpenCC("s2hk")


def to_hk(text):
    return _s2hk.convert(text) if text else text
