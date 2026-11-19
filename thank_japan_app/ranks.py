"""Single source of truth for ranks / levels (2026-11-19).

The 10 ranks double as game levels: each level's minimum cumulative points,
its display (emoji, English + Japanese name) and the question categories it
unlocks in the main timed game all live in RANK_LEVELS below. Everything
that shows or depends on a rank - the header badge (Profile.rank_info), the
account settings rank card/progress bar, the score_* achievements, and the
timeattack question pool - reads from here, so changing a threshold is a
one-line edit.

A player's level never goes down: Profile.max_level stores the highest level
reached, and Profile.level is max(that, the level for the current points) -
points can drop (time-extension purchases), the level can't.

No model imports here, so models.py/achievements.py/views.py can all import
it freely.
"""

RANK_LEVELS = [
    {'level': 1, 'min': 0, 'emoji': '🌾', 'name': 'Villager', 'ja': '村人', 'card': 'card-wood',
     'categories': ['food', 'animal']},
    {'level': 2, 'min': 150, 'emoji': '🎒', 'name': 'Traveler', 'ja': '旅人', 'card': 'card-wood',
     'categories': ['flower', 'cook']},
    {'level': 3, 'min': 350, 'emoji': '🚣', 'name': 'Ronin', 'ja': '浪人', 'card': 'card-silver',
     'categories': ['nature', 'fashion']},
    {'level': 4, 'min': 600, 'emoji': '🥷', 'name': 'Ninja', 'ja': '忍者', 'card': 'card-gold',
     'categories': ['sports', 'householditems']},
    {'level': 5, 'min': 900, 'emoji': '⚔️', 'name': 'Samurai', 'ja': '侍', 'card': 'card-gold',
     'categories': ['appliances', 'building']},
    {'level': 6, 'min': 1300, 'emoji': '🚩', 'name': 'Hatamoto', 'ja': '旗本', 'card': 'card-platinum',
     'categories': ['body', 'dailyactions', 'culture']},
    {'level': 7, 'min': 1800, 'emoji': '🏯', 'name': 'Daimyo', 'ja': '大名', 'card': 'card-platinum',
     'categories': ['live', 'work']},
    {'level': 8, 'min': 2400, 'emoji': '🐎', 'name': 'Shogun', 'ja': '将軍', 'card': 'card-shogun',
     'categories': ['DailyConversation', 'TourismEtiquette', 'Entertainment', 'slang']},
    {'level': 9, 'min': 3100, 'emoji': '👑', 'name': 'General', 'ja': '大将軍', 'card': 'card-shogun',
     'categories': ['LivingInJapan', 'RomanceRelationships', 'BusinessJapanese']},
    {'level': 10, 'min': 4000, 'emoji': '🗾', 'name': 'Legend', 'ja': '伝説', 'card': 'card-shogun',
     'categories': ['MedicalEmergency', 'RealEstateRules', 'Prefectures']},
]
MAX_LEVEL = RANK_LEVELS[-1]['level']
GUEST_LEVEL = 1  # guests can't accumulate points, so they always play the Lv1 pool


def rank_for_level(level):
    level = min(max(int(level or 1), 1), MAX_LEVEL)
    return RANK_LEVELS[level - 1]


def min_points_for_level(level):
    return rank_for_level(level)['min']


def level_for_score(score):
    score = score or 0
    level = 1
    for rank in RANK_LEVELS:
        if score >= rank['min']:
            level = rank['level']
    return level


def categories_for_level(level):
    """Every category unlocked up to and including `level` (cumulative)."""
    level = rank_for_level(level)['level']
    return [cat for rank in RANK_LEVELS[:level] for cat in rank['categories']]


def categories_unlocked_between(old_level, new_level):
    """Categories that became available going from old_level to new_level."""
    return [cat for rank in RANK_LEVELS[old_level:new_level] for cat in rank['categories']]


def rank_progress(level, score):
    """Everything the "Lv5 Samurai - 120pt to the next level" displays need.
    level is the player's (never-decreasing) level, which can sit above the
    level their current points would give - then the bar just reads 0%."""
    rank = rank_for_level(level)
    score = score or 0
    if rank['level'] >= MAX_LEVEL:
        return {'level': rank['level'], 'rank': rank, 'next_rank': None, 'is_max': True,
                'pts_to_next': 0, 'progress_percent': 100}
    next_rank = rank_for_level(rank['level'] + 1)
    span = next_rank['min'] - rank['min']
    progress = (score - rank['min']) / span * 100 if span else 100
    return {
        'level': rank['level'], 'rank': rank, 'next_rank': next_rank, 'is_max': False,
        'pts_to_next': max(next_rank['min'] - score, 0),
        'progress_percent': min(max(progress, 0), 100),
    }


# Short category names for the level-up message (the existing
# format_category filter returns long SEO headlines like "How Do You Say
# 'Food' in Japanese?", which don't read well as a list).
CATEGORY_SHORT_LABELS = {
    'food': ('🍣', {'ja': '食べ物', 'ko': '음식', 'zh-cn': '食物', 'zh-hant': '食物', 'th': 'อาหาร', 'vi': 'Đồ ăn', 'fr': 'Nourriture', 'de': 'Essen', 'it': 'Cibo', 'es': 'Comida', 'pt': 'Comida', 'en': 'Food'}),
    'animal': ('🐶', {'ja': '動物', 'ko': '동물', 'zh-cn': '动物', 'zh-hant': '動物', 'th': 'สัตว์', 'vi': 'Động vật', 'fr': 'Animaux', 'de': 'Tiere', 'it': 'Animali', 'es': 'Animales', 'pt': 'Animais', 'en': 'Animals'}),
    'flower': ('🌸', {'ja': '花', 'ko': '꽃', 'zh-cn': '花卉', 'zh-hant': '花卉', 'th': 'ดอกไม้', 'vi': 'Hoa', 'fr': 'Fleurs', 'de': 'Blumen', 'it': 'Fiori', 'es': 'Flores', 'pt': 'Flores', 'en': 'Flowers'}),
    'cook': ('🍳', {'ja': '料理', 'ko': '요리', 'zh-cn': '烹饪', 'zh-hant': '烹飪', 'th': 'การทำอาหาร', 'vi': 'Nấu ăn', 'fr': 'Cuisine', 'de': 'Kochen', 'it': 'Cucina', 'es': 'Cocina', 'pt': 'Culinária', 'en': 'Cooking'}),
    'nature': ('🌳', {'ja': '自然', 'ko': '자연', 'zh-cn': '自然', 'zh-hant': '自然', 'th': 'ธรรมชาติ', 'vi': 'Thiên nhiên', 'fr': 'Nature', 'de': 'Natur', 'it': 'Natura', 'es': 'Naturaleza', 'pt': 'Natureza', 'en': 'Nature'}),
    'fashion': ('👘', {'ja': 'ファッション', 'ko': '패션', 'zh-cn': '时尚', 'zh-hant': '時尚', 'th': 'แฟชั่น', 'vi': 'Thời trang', 'fr': 'Mode', 'de': 'Mode', 'it': 'Moda', 'es': 'Moda', 'pt': 'Moda', 'en': 'Fashion'}),
    'sports': ('⚽', {'ja': 'スポーツ', 'ko': '스포츠', 'zh-cn': '运动', 'zh-hant': '運動', 'th': 'กีฬา', 'vi': 'Thể thao', 'fr': 'Sport', 'de': 'Sport', 'it': 'Sport', 'es': 'Deportes', 'pt': 'Esportes', 'en': 'Sports'}),
    'householditems': ('🧺', {'ja': '日用品', 'ko': '생활용품', 'zh-cn': '家庭用品', 'zh-hant': '家庭用品', 'th': 'ของใช้ในบ้าน', 'vi': 'Đồ gia dụng', 'fr': 'Articles ménagers', 'de': 'Haushaltsartikel', 'it': 'Articoli per la casa', 'es': 'Artículos del hogar', 'pt': 'Utensílios domésticos', 'en': 'Household Items'}),
    'appliances': ('📺', {'ja': '家電', 'ko': '가전제품', 'zh-cn': '家电', 'zh-hant': '家電', 'th': 'เครื่องใช้ไฟฟ้า', 'vi': 'Đồ điện gia dụng', 'fr': 'Électroménager', 'de': 'Haushaltsgeräte', 'it': 'Elettrodomestici', 'es': 'Electrodomésticos', 'pt': 'Eletrodomésticos', 'en': 'Appliances'}),
    'building': ('🏯', {'ja': '建物', 'ko': '건물', 'zh-cn': '建筑', 'zh-hant': '建築', 'th': 'อาคาร', 'vi': 'Tòa nhà', 'fr': 'Bâtiments', 'de': 'Gebäude', 'it': 'Edifici', 'es': 'Edificios', 'pt': 'Construções', 'en': 'Buildings'}),
    'body': ('💪', {'ja': '体', 'ko': '신체', 'zh-cn': '身体', 'zh-hant': '身體', 'th': 'ร่างกาย', 'vi': 'Cơ thể', 'fr': 'Corps', 'de': 'Körper', 'it': 'Corpo', 'es': 'Cuerpo', 'pt': 'Corpo', 'en': 'Body'}),
    'dailyactions': ('🚶', {'ja': '日常の動作', 'ko': '일상 동작', 'zh-cn': '日常动作', 'zh-hant': '日常動作', 'th': 'การกระทำประจำวัน', 'vi': 'Hành động hằng ngày', 'fr': 'Actions quotidiennes', 'de': 'Alltagshandlungen', 'it': 'Azioni quotidiane', 'es': 'Acciones diarias', 'pt': 'Ações diárias', 'en': 'Daily Actions'}),
    'culture': ('🎎', {'ja': '文化', 'ko': '문화', 'zh-cn': '文化', 'zh-hant': '文化', 'th': 'วัฒนธรรม', 'vi': 'Văn hóa', 'fr': 'Culture', 'de': 'Kultur', 'it': 'Cultura', 'es': 'Cultura', 'pt': 'Cultura', 'en': 'Culture'}),
    'live': ('🏠', {'ja': '生活', 'ko': '생활', 'zh-cn': '生活', 'zh-hant': '生活', 'th': 'ชีวิตประจำวัน', 'vi': 'Cuộc sống', 'fr': 'Vie quotidienne', 'de': 'Alltag', 'it': 'Vita quotidiana', 'es': 'Vida cotidiana', 'pt': 'Dia a dia', 'en': 'Everyday Life'}),
    'work': ('💼', {'ja': '仕事', 'ko': '일', 'zh-cn': '工作', 'zh-hant': '工作', 'th': 'งาน', 'vi': 'Công việc', 'fr': 'Travail', 'de': 'Arbeit', 'it': 'Lavoro', 'es': 'Trabajo', 'pt': 'Trabalho', 'en': 'Work'}),
    'DailyConversation': ('💬', {'ja': '日常会話', 'ko': '일상 대화', 'zh-cn': '日常对话', 'zh-hant': '日常對話', 'th': 'บทสนทนาประจำวัน', 'vi': 'Hội thoại hằng ngày', 'fr': 'Conversation courante', 'de': 'Alltagsgespräche', 'it': 'Conversazione quotidiana', 'es': 'Conversación diaria', 'pt': 'Conversa do dia a dia', 'en': 'Everyday Conversation'}),
    'TourismEtiquette': ('🗺️', {'ja': '観光マナー', 'ko': '관광 매너', 'zh-cn': '旅游礼仪', 'zh-hant': '旅遊禮儀', 'th': 'มารยาทการท่องเที่ยว', 'vi': 'Phép lịch sự khi du lịch', 'fr': 'Étiquette touristique', 'de': 'Reiseknigge', 'it': 'Galateo turistico', 'es': 'Etiqueta turística', 'pt': 'Etiqueta turística', 'en': 'Tourism Etiquette'}),
    'Entertainment': ('🎮', {'ja': 'エンタメ', 'ko': '엔터테인먼트', 'zh-cn': '娱乐', 'zh-hant': '娛樂', 'th': 'ความบันเทิง', 'vi': 'Giải trí', 'fr': 'Divertissement', 'de': 'Unterhaltung', 'it': 'Intrattenimento', 'es': 'Entretenimiento', 'pt': 'Entretenimento', 'en': 'Entertainment'}),
    'slang': ('😎', {'ja': 'スラング', 'ko': '슬랭', 'zh-cn': '俚语', 'zh-hant': '俚語', 'th': 'สแลง', 'vi': 'Tiếng lóng', 'fr': 'Argot', 'de': 'Slang', 'it': 'Slang', 'es': 'Jerga', 'pt': 'Gírias', 'en': 'Slang'}),
    'LivingInJapan': ('🗾', {'ja': '日本での生活', 'ko': '일본 생활', 'zh-cn': '日本生活', 'zh-hant': '日本生活', 'th': 'ชีวิตในญี่ปุ่น', 'vi': 'Sống ở Nhật Bản', 'fr': 'Vivre au Japon', 'de': 'Leben in Japan', 'it': 'Vivere in Giappone', 'es': 'Vivir en Japón', 'pt': 'Viver no Japão', 'en': 'Living in Japan'}),
    'RomanceRelationships': ('💕', {'ja': '恋愛・人間関係', 'ko': '연애·인간관계', 'zh-cn': '恋爱与人际关系', 'zh-hant': '戀愛與人際關係', 'th': 'ความรักและความสัมพันธ์', 'vi': 'Tình yêu & các mối quan hệ', 'fr': 'Amour et relations', 'de': 'Liebe & Beziehungen', 'it': 'Amore e relazioni', 'es': 'Amor y relaciones', 'pt': 'Amor e relacionamentos', 'en': 'Romance & Relationships'}),
    'BusinessJapanese': ('👔', {'ja': 'ビジネス日本語', 'ko': '비즈니스 일본어', 'zh-cn': '商务日语', 'zh-hant': '商務日語', 'th': 'ภาษาญี่ปุ่นธุรกิจ', 'vi': 'Tiếng Nhật thương mại', 'fr': 'Japonais des affaires', 'de': 'Japanisch für den Beruf', 'it': 'Giapponese per il lavoro', 'es': 'Japonés de negocios', 'pt': 'Japonês para negócios', 'en': 'Business Japanese'}),
    'MedicalEmergency': ('🚑', {'ja': '医療・救急', 'ko': '의료·응급', 'zh-cn': '医疗急救', 'zh-hant': '醫療急救', 'th': 'การแพทย์และเหตุฉุกเฉิน', 'vi': 'Y tế & Cấp cứu', 'fr': 'Santé et urgences', 'de': 'Medizin & Notfall', 'it': 'Medicina ed emergenze', 'es': 'Medicina y emergencias', 'pt': 'Saúde e emergências', 'en': 'Medical & Emergency'}),
    'RealEstateRules': ('🏢', {'ja': '不動産', 'ko': '부동산', 'zh-cn': '房地产', 'zh-hant': '房地產', 'th': 'อสังหาริมทรัพย์', 'vi': 'Bất động sản', 'fr': 'Immobilier', 'de': 'Immobilien', 'it': 'Immobiliare', 'es': 'Inmobiliaria', 'pt': 'Imóveis', 'en': 'Real Estate'}),
    'Prefectures': ('📍', {'ja': '都道府県', 'ko': '도도부현', 'zh-cn': '都道府县', 'zh-hant': '都道府縣', 'th': 'จังหวัด', 'vi': 'Tỉnh thành', 'fr': 'Préfectures', 'de': 'Präfekturen', 'it': 'Prefetture', 'es': 'Prefecturas', 'pt': 'Províncias', 'en': 'Prefectures'}),
}

_LABEL_LANG_ALIAS = {'es-es': 'es', 'es-mx': 'es', 'pt-br': 'pt', 'en-in': 'en'}


def category_short_label(category, lang_code):
    emoji, names = CATEGORY_SHORT_LABELS.get(category, ('', {}))
    lang = _LABEL_LANG_ALIAS.get(lang_code, lang_code)
    name = names.get(lang) or names.get('en') or category
    return f'{emoji} {name}'.strip()
