"""三国杀军争版：武将池、牌堆与技能元数据（供 games.SanguoshaGame 使用）。"""

from __future__ import annotations

from typing import Optional

# (姓名, 体力, 势力, 技能ID元组, 简介)
_SGS_GENERAL_ROWS: list[tuple[str, int, str, tuple[str, ...], str]] = [
    # —— 魏 ——
    ("曹操", 4, "魏", ("jianxiong",), "奸雄：受伤后获得伤害牌或摸1张"),
    ("司马懿", 3, "魏", ("fankui",), "反馈：受伤后从伤害来源摸1张"),
    ("夏侯惇", 4, "魏", ("ganglie",), "刚烈：受伤后对来源造成1点伤害"),
    ("张辽", 4, "魏", ("tuxi",), "突袭：/game move 突袭 <目标>"),
    ("许褚", 4, "魏", ("luoyi",), "裸衣：/game move 裸衣 后下一张【杀】+1伤"),
    ("郭嘉", 3, "魏", ("yiji",), "遗计：受伤后摸2张"),
    ("甄姬", 3, "魏", ("luoshen",), "洛神：摸牌阶段连续翻黑色牌入手"),
    ("曹丕", 3, "魏", ("fangzhu",), "放逐：造成伤害令目标弃1张手牌"),
    ("张郃", 4, "魏", ("qiaobian",), "巧变：/game move 巧变 <牌>"),
    ("徐晃", 4, "魏", ("duanliang",), "断粮：黑色牌当【兵粮】/game move 断粮 <目标> <牌>"),
    ("典韦", 4, "魏", ("qiangxi",), "强袭：/game move 强袭 <目标> <牌>"),
    ("荀彧", 3, "魏", ("jizhi",), "集智：使用锦囊后摸1张"),
    ("邓艾", 4, "魏", ("zaoxian",), "凿险：手牌为空时觉醒+1体力并多摸1张"),
    ("张春华", 3, "魏", ("shangshi",), "伤逝：失去体力后若手牌≤体力则摸1张（可重复）"),
    # —— 蜀 ——
    ("刘备", 4, "蜀", ("rende",), "仁德：/game move 仁德 <目标> <牌>"),
    ("关羽", 4, "蜀", ("wusheng",), "武圣：红色牌当【杀】"),
    ("张飞", 4, "蜀", ("paoxiao",), "咆哮：【杀】无次数限制"),
    ("诸葛亮", 3, "蜀", ("guanxing",), "观星：摸牌前看牌堆顶5张并调整顺序"),
    ("赵云", 4, "蜀", ("longdan",), "龙胆：【杀】与【闪】可互换"),
    ("马超", 4, "蜀", ("tieqi",), "铁骑：你使用的【杀】不可被【闪】抵消"),
    ("黄忠", 4, "蜀", ("liegong",), "烈弓：杀体力≥你的+1伤；≤你的不可闪"),
    ("黄月英", 3, "蜀", ("jizhi",), "集智：使用锦囊后摸1张"),
    ("庞统", 3, "蜀", ("niepan",), "涅槃：首次濒死回复至1体力"),
    ("魏延", 4, "蜀", ("kuanggu",), "狂骨：相邻角色受伤后你回复1点"),
    ("姜维", 4, "蜀", ("guanxing",), "观星：摸牌前看牌堆顶5张并调整顺序"),
    ("刘禅", 3, "蜀", ("xiangle",), "享乐：成为【杀】目标时可弃2张抵消"),
    ("孟获", 4, "蜀", ("huoshou",), "祸首：【南蛮】无效；击杀角色摸2张"),
    ("祝融", 4, "蜀", ("lieren",), "烈刃：【杀】造成伤害后获得目标1张手牌"),
    # —— 吴 ——
    ("孙权", 4, "吴", ("zhiheng",), "制衡：/game move 制衡 <牌>"),
    (
        "周瑜",
        3,
        "吴",
        ("yingzi", "fanjian"),
        "英姿：摸牌阶段多摸1张；"
        "反间：/game move 反间 <目标> <其选花色> <你交出的牌>",
    ),
    ("孙尚香", 3, "吴", ("jieyin",), "结姻：/game move 结姻 <目标>（双方≤2体力）"),
    ("陆逊", 3, "吴", ("qianxun",), "谦逊：无手牌时不能成为锦囊目标"),
    (
        "大乔",
        3,
        "吴",
        ("guose", "liuli"),
        "国色：方块牌当【乐不思蜀】/game move 国色 <目标> <方块牌>；"
        "流离：成为【杀】目标时可弃1张将【杀】转移他人 /game move 流离 <牌> <目标>",
    ),
    ("小乔", 3, "吴", ("tianxiang",), "天香：/game move 天香 <牌> <转移目标>"),
    ("太史慈", 4, "吴", ("tianyi",), "天义：每回合2张【杀】"),
    ("周泰", 4, "吴", ("buqu",), "不屈：+1体力上限"),
    ("鲁肃", 3, "吴", ("haoshi",), "好施：多摸2张并分给手牌最少者"),
    ("孙坚", 4, "吴", ("yinghun",), "英魂：/game move 英魂 己|他 <目标>"),
    ("孙策", 4, "吴", ("paoxiao", "yingzi"), "霸王：咆哮+英姿"),
    ("甘宁", 4, "吴", ("qixi",), "奇袭：黑色牌当【过河拆桥】/game move 奇袭"),
    # —— 群 ——
    ("吕布", 4, "群", ("wushuang",), "无双：对你【杀】/【决斗】需连续2张【闪】/【杀】"),
    ("貂蝉", 3, "群", ("biyue",), "闭月：结束阶段无手牌则摸1张"),
    ("华佗", 3, "群", ("qingnang",), "青囊：/game move 青囊 <目标> <牌>"),
    ("袁绍", 4, "群", ("luanji",), "乱击：/game move 乱击 <牌1> <牌2>（当万箭）"),
    ("贾诩", 3, "群", ("wansha",), "完杀：你的回合内他人不能用【桃】救体力1"),
    ("张角", 3, "群", ("leiji",), "雷击：/game move 雷击 <目标>"),
    ("颜良文丑", 4, "群", ("shuangxiong",), "双雄：/game move 双雄 红|黑 后用异色牌当【决斗】"),
    ("蔡文姬", 3, "群", ("benggu",), "断肠：你阵亡后凶手失去所有技能"),
    ("左慈", 3, "群", ("huashen",), "化身：额外获得一名随机武将的技能"),
    ("于吉", 3, "群", ("guhuo",), "蛊惑：/game move 蛊惑 <锦囊> [参数…] <牌>"),
    ("董卓", 8, "群", ("jiuchi",), "酒池：【梅花】牌可当【酒】"),
    ("公孙瓒", 4, "群", ("yicong",), "义从：与所有人生座次距离≥2时多摸1张"),
]

SGS_GENERAL_BY_NAME: dict[str, dict] = {}
for _name, _hp, _kingdom, _skills, _desc in _SGS_GENERAL_ROWS:
    SGS_GENERAL_BY_NAME[_name] = {
        "name": _name,
        "hp": _hp,
        "kingdom": _kingdom,
        "skills": _skills,
        "desc": _desc,
    }

SGS_GENERAL_POOL: list[dict] = list(SGS_GENERAL_BY_NAME.values())

# 技能 ID → 显示名（/game show 等）
SGS_SKILL_LABELS: dict[str, str] = {
    "jianxiong": "奸雄",
    "fankui": "反馈",
    "ganglie": "刚烈",
    "tuxi": "突袭",
    "luoyi": "裸衣",
    "yiji": "遗计",
    "luoshen": "洛神",
    "fangzhu": "放逐",
    "qiaobian": "巧变",
    "duanliang": "断粮",
    "qiangxi": "强袭",
    "jizhi": "集智",
    "zaoxian": "凿险",
    "shangshi": "伤逝",
    "rende": "仁德",
    "wusheng": "武圣",
    "paoxiao": "咆哮",
    "guanxing": "观星",
    "longdan": "龙胆",
    "tieqi": "铁骑",
    "liegong": "烈弓",
    "niepan": "涅槃",
    "kuanggu": "狂骨",
    "xiangle": "享乐",
    "huoshou": "祸首",
    "lieren": "烈刃",
    "zhiheng": "制衡",
    "yingzi": "英姿",
    "fanjian": "反间",
    "jieyin": "结姻",
    "qianxun": "谦逊",
    "guose": "国色",
    "liuli": "流离",
    "tianxiang": "天香",
    "tianyi": "天义",
    "buqu": "不屈",
    "haoshi": "好施",
    "yinghun": "英魂",
    "qixi": "奇袭",
    "wushuang": "无双",
    "biyue": "闭月",
    "qingnang": "青囊",
    "luanji": "乱击",
    "wansha": "完杀",
    "leiji": "雷击",
    "shuangxiong": "双雄",
    "benggu": "断肠",
    "huashen": "化身",
    "guhuo": "蛊惑",
    "jiuchi": "酒池",
    "yicong": "义从",
}


def format_skills(skill_ids: tuple[str, ...]) -> str:
    return "、".join(SGS_SKILL_LABELS.get(s, s) for s in skill_ids)


CARD_SEP = "·"
RED_SUITS = frozenset({"红桃", "方块"})
BLACK_SUITS = frozenset({"黑桃", "梅花"})
ALL_SUITS: tuple[str, ...] = ("红桃", "方块", "黑桃", "梅花")

SHA_CARDS = frozenset({"杀", "火杀", "雷杀"})
SHAN_CARDS = frozenset({"闪"})
TAO_CARDS = frozenset({"桃"})
TRICK_NAMES = frozenset({
    "决斗",
    "过河拆桥",
    "无中生有",
    "南蛮入侵",
    "万箭齐发",
    "顺手牵羊",
    "兵粮寸断",
    "铁索连环",
    "五谷丰登",
    "桃园结义",
    "火攻",
})


def card_base(card: str) -> str:
    if CARD_SEP in card:
        return card.split(CARD_SEP, 1)[0]
    return card


def card_suit(card: str) -> str:
    if CARD_SEP in card:
        return card.split(CARD_SEP, 1)[1]
    return ""


def is_red(card: str) -> bool:
    return card_suit(card) in RED_SUITS


def is_black(card: str) -> bool:
    return card_suit(card) in BLACK_SUITS


def is_diamond(card: str) -> bool:
    return card_suit(card) == "方块"


def card_label(card: str) -> str:
    suit = card_suit(card)
    base = card_base(card)
    return f"{suit}{base}" if suit else base


def card_matches(hand_card: str, token: str) -> bool:
    token = token.strip()
    if not token:
        return False
    if hand_card == token or card_base(hand_card) == token:
        return True
    if card_label(hand_card) == token:
        return True
    suit, base = card_suit(hand_card), card_base(hand_card)
    return bool(suit) and f"{suit}{base}" == token


def find_card_in_hand(hand: list[str], token: str) -> Optional[str]:
    for c in hand:
        if card_matches(c, token):
            return c
    return None


# 装备：武器名 → 攻击距离（不含进攻马 +1）
SGS_WEAPON_RANGE: dict[str, int] = {
    "诸葛连弩": 1,
    "雌雄双股剑": 2,
    "青釭剑": 2,
    "青龙偃月刀": 3,
    "丈八蛇矛": 3,
    "贯石斧": 3,
    "方天画戟": 4,
    "麒麟弓": 5,
    "古锭刀": 2,
    "寒冰剑": 2,
    "朱雀羽扇": 4,
}

SGS_ARMOR_NAMES = frozenset({"八卦阵", "仁王盾", "藤甲", "白银狮子"})
SGS_HORSE_PLUS_NAMES = frozenset({"绝影", "的卢", "骅骝", "爪黄飞电"})
SGS_HORSE_MINUS_NAMES = frozenset({"赤兔", "紫騂", "大宛"})

SGS_ALL_EQUIP_NAMES = (
    frozenset(SGS_WEAPON_RANGE)
    | SGS_ARMOR_NAMES
    | SGS_HORSE_PLUS_NAMES
    | SGS_HORSE_MINUS_NAMES
)

_EQUIP_DECK_COUNTS: list[tuple[str, int]] = [
    ("诸葛连弩", 1),
    ("雌雄双股剑", 1),
    ("青釭剑", 1),
    ("青龙偃月刀", 1),
    ("丈八蛇矛", 1),
    ("贯石斧", 1),
    ("方天画戟", 1),
    ("麒麟弓", 1),
    ("古锭刀", 1),
    ("寒冰剑", 1),
    ("朱雀羽扇", 1),
    ("八卦阵", 2),
    ("仁王盾", 1),
    ("藤甲", 2),
    ("白银狮子", 1),
    ("紫騂", 1),
    ("大宛", 1),
    ("绝影", 1),
    ("的卢", 1),
    ("赤兔", 1),
    ("骅骝", 1),
    ("爪黄飞电", 1),
]


def is_equipment(card: str) -> bool:
    return card_base(card) in SGS_ALL_EQUIP_NAMES


def equip_slot(card: str) -> Optional[str]:
    """返回 weapon / armor / horse_plus / horse_minus，非装备则 None。"""
    base = card_base(card)
    if base in SGS_WEAPON_RANGE:
        return "weapon"
    if base in SGS_ARMOR_NAMES:
        return "armor"
    if base in SGS_HORSE_PLUS_NAMES:
        return "horse_plus"
    if base in SGS_HORSE_MINUS_NAMES:
        return "horse_minus"
    return None


def weapon_range(card: Optional[str]) -> int:
    if not card:
        return 1
    return SGS_WEAPON_RANGE.get(card_base(card), 1)


def equip_short_label(card: Optional[str]) -> str:
    if not card:
        return "—"
    return card_label(card)


def build_junzheng_deck() -> list[str]:
    names: list[str] = []
    names += ["杀"] * 8
    names += ["火杀"] * 5
    names += ["雷杀"] * 3
    names += ["闪"] * 8
    names += ["桃"] * 6
    names += ["酒"] * 4
    names += ["决斗"] * 2
    names += ["过河拆桥"] * 3
    names += ["顺手牵羊"] * 2
    names += ["无中生有"] * 3
    names += ["南蛮入侵"] * 2
    names += ["万箭齐发"] * 2
    names += ["桃园结义"] * 1
    names += ["五谷丰登"] * 1
    names += ["兵粮寸断"] * 2
    names += ["铁索连环"] * 2
    names += ["火攻"] * 2
    for eq_name, count in _EQUIP_DECK_COUNTS:
        names += [eq_name] * count
    deck: list[str] = []
    for i, name in enumerate(names):
        suit = ALL_SUITS[i % len(ALL_SUITS)]
        deck.append(f"{name}{CARD_SEP}{suit}")
    return deck


def format_general_list() -> list[str]:
    by_k: dict[str, list[str]] = {"魏": [], "蜀": [], "吴": [], "群": []}
    for g in SGS_GENERAL_POOL:
        by_k.setdefault(g["kingdom"], []).append(g["name"])
    lines = [
        f"军争武将池共 {len(SGS_GENERAL_POOL)} 名（开局随机不重复分配）：",
    ]
    for k in ("魏", "蜀", "吴", "群"):
        names = "、".join(sorted(by_k.get(k, [])))
        lines.append(f"  【{k}】{names}")
    lines.append("  开局后 /game show 查看己方武将技能。")
    return lines
