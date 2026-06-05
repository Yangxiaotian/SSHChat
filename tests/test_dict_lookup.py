"""Tests for dictionary lookup formatting."""
import unittest

import dict_lookup


HELLO_RESPONSE = {
    "simple": {
        "word": [{"usphone": "həˈloʊ", "ukphone": "həˈləʊ"}],
    },
    "ec": {
        "word": [
            {
                "trs": [
                    {"tr": [{"l": {"i": "int. 喂，你好"}}]},
                    {"tr": [{"l": {"i": "n. 招呼，问候"}}]},
                ]
            }
        ]
    },
}

NIHAO_RESPONSE = {
    "simple": {"word": [{"phone": "nǐ hǎo"}]},
    "ce": {
        "word": [
            {
                "trs": [
                    {
                        "tr": [
                            {
                                "l": {
                                    "i": ["", {"#text": "hello"}],
                                    "#tran": "喂，你好；",
                                }
                            }
                        ]
                    }
                ]
            }
        ]
    },
}

XUEXI_RESPONSE = {
    "newhh": {
        "source": {"name": "《现代汉语规范词典》"},
        "dataList": [
            {
                "word": "学习",
                "pinyin": "xuéxí",
                "sense": [
                    {
                        "cat": "动词",
                        "def": ["通过读书、听课、研究、实践等手段获取知识或技能"],
                        "examples": ["<self>学习</self>语言"],
                    }
                ],
            }
        ],
    }
}


def _fake_fetch(word: str) -> dict:
    mapping = {
        "hello": HELLO_RESPONSE,
        "你好": NIHAO_RESPONSE,
        "学习": XUEXI_RESPONSE,
    }
    if word not in mapping:
        raise RuntimeError("not found")
    return mapping[word]


class TestDictLookup(unittest.TestCase):
    def test_detect_mode(self) -> None:
        self.assertEqual(dict_lookup.detect_mode("hello"), "en")
        self.assertEqual(dict_lookup.detect_mode("你好"), "cn")

    def test_normalize_mode(self) -> None:
        self.assertEqual(dict_lookup.normalize_mode("英"), "en")
        self.assertEqual(dict_lookup.normalize_mode("汉语"), "hh")

    def test_en_zh_format(self) -> None:
        lines = dict_lookup.lookup_lines("en", "hello", fetch=_fake_fetch)
        text = "\n".join(lines)
        self.assertIn("英→中", text)
        self.assertIn("喂，你好", text)
        self.assertIn("həˈloʊ", text)

    def test_zh_en_format(self) -> None:
        lines = dict_lookup.lookup_lines("cn", "你好", fetch=_fake_fetch)
        text = "\n".join(lines)
        self.assertIn("中→英", text)
        self.assertIn("hello", text)

    def test_zh_zh_format(self) -> None:
        lines = dict_lookup.lookup_lines("hh", "学习", fetch=_fake_fetch)
        text = "\n".join(lines)
        self.assertIn("汉语", text)
        self.assertIn("xuéxí", text)
        self.assertIn("通过读书", text)
        self.assertNotIn("<self>", text)


if __name__ == "__main__":
    unittest.main()
