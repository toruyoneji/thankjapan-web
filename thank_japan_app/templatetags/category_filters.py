from django import template

from django.utils.safestring import mark_safe
import re

register = template.Library()

@register.filter
def format_category(value, lang_code='en'):
    if not value:
        return value

    
    val_key = str(value).lower().replace(" ", "")

    
    translations = {
        'culture': {'ja': '文化', 'en': 'Culture', 'de': 'Kultur', 'fr': 'Culture', 'es': 'Cultura', 'it': 'Cultura', 'ko': '문화', 'zh-hans': '文化', 'zh-hant': '文化', 'vi': 'Văn hóa', 'th': 'วัฒนธรรม', 'pt': 'Cultura'},
        'food': {'ja': '食べ物', 'en': 'Food', 'de': 'Essen', 'fr': 'Nourriture', 'es': 'Comida', 'it': 'Cibo', 'ko': '음식', 'zh-hans': '食物', 'zh-hant': '食物', 'vi': 'Thức ăn', 'th': 'อาหาร', 'pt': 'Comida'},
        'cook': {'ja': '料理', 'en': 'Cooking', 'de': 'Kochen', 'fr': 'Cuisine', 'es': 'Cocina', 'it': 'Cucina', 'ko': '요리', 'zh-hans': '烹饪', 'zh-hant': '烹飪', 'vi': 'Nấu ăn', 'th': 'การทำอาหาร', 'pt': 'Culinária'},
        'fashion': {'ja': 'ファッション', 'en': 'Fashion', 'de': 'Mode', 'fr': 'Mode', 'es': 'Moda', 'it': 'Moda', 'ko': '패션', 'zh-hans': '时尚', 'zh-hant': '時尚', 'vi': 'Thời trang', 'th': 'แฟชั่น', 'pt': 'Moda'},
        'nature': {'ja': '自然', 'en': 'Nature', 'de': 'Natur', 'fr': 'Nature', 'es': 'Naturaleza', 'it': 'Natura', 'ko': '자연', 'zh-hans': '自然', 'zh-hant': '自然', 'vi': 'Tự nhiên', 'th': 'ธรรมชาติ', 'pt': 'Natureza'},
        'animal': {'ja': '動物', 'en': 'Animal', 'de': 'Tier', 'fr': 'Animal', 'es': 'Animal', 'it': 'Animale', 'ko': '동물', 'zh-hans': '动物', 'zh-hant': '動物', 'vi': 'Động vật', 'th': 'สัตว์', 'pt': 'Animal'},
        'sports': {'ja': 'スポーツ', 'en': 'Sports', 'de': 'Sport', 'fr': 'Sports', 'es': 'Deportes', 'it': 'Sport', 'ko': '스포츠', 'zh-hans': '体育', 'zh-hant': '體育', 'vi': 'Thể thao', 'th': 'กีฬา', 'pt': 'Esportes'},
        'householditems': {'ja': '家庭用品', 'en': 'Household Items', 'de': 'Haushaltsgegenstände', 'fr': 'Articles ménagers', 'es': 'Artículos del hogar', 'it': 'Articoli per la casa', 'ko': '가정용품', 'zh-hans': '家庭用品', 'zh-hant': '家庭用品', 'vi': 'Đồ dùng gia đình', 'th': 'ของใช้ในครัวเรือน', 'pt': 'Itens domésticos'},
        'appliances': {'ja': '家電', 'en': 'Appliances', 'de': 'Haushaltsgeräte', 'fr': 'Appareils', 'es': 'Electrodomésticos', 'it': 'Elettrodomestici', 'ko': '가전제품', 'zh-hans': '家电', 'zh-hant': '家電', 'vi': 'Thiết bị', 'th': 'เครื่องใช้ไฟฟ้า', 'pt': 'Eletrodomésticos'},
        'building': {'ja': '建物', 'en': 'Building', 'de': 'Gebäude', 'fr': 'Bâtiment', 'es': 'Edificio', 'it': 'Edificio', 'ko': '건물', 'zh-hans': '建筑物', 'zh-hant': '建築物', 'vi': 'Tòa nhà', 'th': 'อาคาร', 'pt': 'Construção'},
        'flower': {'ja': '花', 'en': 'Flower', 'de': 'Blume', 'fr': 'Fleur', 'es': 'Flor', 'it': 'Fiore', 'ko': '꽃', 'zh-hans': '花卉', 'zh-hant': '花卉', 'vi': 'Hoa', 'th': 'ดอกไม้', 'pt': 'Flor'},
        'work': {'ja': '仕事', 'en': 'Work', 'de': 'Arbeit', 'fr': 'Travail', 'es': 'Trabajo', 'it': 'Lavoro', 'ko': '일', 'zh-hans': '工作', 'zh-hant': '工作', 'vi': 'Công việc', 'th': 'งาน', 'pt': 'Trabalho'},
        'live': {'ja': '生活', 'en': 'Life', 'de': 'Leben', 'fr': 'Vie', 'es': 'Vida', 'it': 'Vita', 'ko': '생활', 'zh-hans': '生活', 'zh-hant': '生活', 'vi': 'Cuộc sống', 'th': 'ชีวิต', 'pt': 'Vida'},
        'body': {'ja': '体', 'en': 'Body', 'de': 'Körper', 'fr': 'Corps', 'es': 'Cuerpo', 'it': 'Corpo', 'ko': '신체', 'zh-hans': '身体', 'zh-hant': '身體', 'vi': 'Cơ thể', 'th': 'ร่างกาย', 'pt': 'Corpo'},
        'dailyactions': {'ja': '日常の動作', 'en': 'Daily Actions', 'de': 'Tägliche Handlungen', 'fr': 'Actions quotidiennes', 'es': 'Acciones diarias', 'it': 'Azioni quotidiane', 'ko': '일상적인 동작', 'zh-hans': '日常动作', 'zh-hant': '日常動作', 'vi': 'Hành động hàng ngày', 'th': 'กิจวัตรประจำวัน', 'pt': 'Ações diárias'},
        'dailyconversation': {'ja': '日常会話', 'en': 'Daily Conversation', 'de': 'Alltagskonversation', 'fr': 'Conversation quotidienne', 'es': 'Conversación diaria', 'it': 'Conversazione quotidiana', 'ko': '일상 대話', 'zh-hans': '日常对话', 'zh-hant': '日常對話', 'vi': 'Hội thoại hàng ngày', 'th': 'บทสนทนาประจำวัน', 'pt': 'Conversa diária'},
        'businessjapanese': {'ja': 'ビジネス日本語', 'en': 'Business Japanese', 'de': 'Business-Japanisch', 'fr': 'Japonais des affaires', 'es': 'Japonés de negocios', 'it': 'Giapponese commerciale', 'ko': '비즈니스 일본어', 'zh-hans': '商务日语', 'zh-hant': '商務日語', 'vi': 'Tiếng Nhật kinh doanh', 'th': 'ภาษาญี่ปุ่นธุรกิจ', 'pt': 'Japonês para negócios'},
        'livinginjapan': {'ja': '日本での生活', 'en': 'Living in Japan', 'de': 'Leben in Japan', 'fr': 'Vivre au Japon', 'es': 'Vivir en Japón', 'it': 'Vivere in Giappone', 'ko': '일본 생활', 'zh-hans': '日本生活', 'zh-hant': '日本生活', 'vi': 'Sống tại Nhật Bản', 'th': 'การใช้ชีวิตในญี่ปุ่น', 'pt': 'Morar no Japão'},
        'medicalemergency': {'ja': '医療・救急', 'en': 'Medical Emergency', 'de': 'Medizinischer Notfall', 'fr': 'Urgence médicale', 'es': 'Emergencia médica', 'it': 'Emergenza medica', 'ko': '의료 응급', 'zh-hans': '医疗急救', 'zh-hant': '醫療急救', 'vi': 'Cấp cứu y tế', 'th': 'เหตุฉุกเฉินทางการแพทย์', 'pt': 'Emergência médica'},
        'realestaterules': {'ja': '不動産のルール', 'en': 'Real Estate Rules', 'de': 'Immobilienregeln', 'fr': 'Règles immobilières', 'es': 'Reglas inmobiliarias', 'it': 'Regole immobiliari', 'ko': '부동산 규칙', 'zh-hans': '房地产规则', 'zh-hant': '房地產規則', 'vi': 'Quy tắc bất động sản', 'th': 'กฎเกณฑ์อสังหาริมทรัพย์', 'pt': 'Regras imobiliárias'},
        'tourismetiquette': {'ja': '観光マナー', 'en': 'Tourism Etiquette', 'de': 'Tourismus-Etikette', 'fr': 'Étiquette touristique', 'es': 'Etiqueta turística', 'it': 'Galateo turistico', 'ko': '관광 에티켓', 'zh-hans': '旅游礼仪', 'zh-hant': '旅遊禮儀', 'vi': 'Nghi thức du lịch', 'th': 'มารยาทการท่องเที่ยว', 'pt': 'Etiqueta turística'},
        'prefectures': {'ja': '都道府県', 'en': 'Prefectures', 'de': 'Präfekturen', 'fr': 'Préfectures', 'es': 'Prefecturas', 'it': 'Prefetture', 'ko': '도도부현', 'zh-hans': '都道府县', 'zh-hant': '都道府縣', 'vi': 'Tỉnh thành', 'th': 'จังหวัด', 'pt': 'Províncias'},
        'entertainment': {'ja': '娯楽', 'en': 'Entertainment', 'de': 'Unterhaltung', 'fr': 'Divertissement', 'es': 'Entretenimiento', 'it': 'Intrattenimento', 'ko': '엔터테인먼트', 'zh-hans': '娱乐', 'zh-hant': '娛樂', 'vi': 'Giải trí', 'th': 'ความบันเทิง', 'pt': 'Entretenimento'},
        'slang': {'ja': 'スラング', 'en': 'Slang', 'de': 'Slang', 'fr': 'Argot', 'es': 'Argot', 'it': 'Gergo', 'ko': '속어', 'zh-hans': '俚语', 'zh-hant': '俚語', 'vi': 'Tiếng lóng', 'th': 'สแลง', 'pt': 'Gíria'},
    }

    
    lang = lang_code.lower()
    if 'zh-cn' in lang: lang_key = 'zh-hans'
    elif 'zh-hant' in lang: lang_key = 'zh-hant'
    elif 'es-' in lang: lang_key = 'es'      
    elif 'pt-' in lang or lang == 'pt': lang_key = 'pt' 
    elif 'en-' in lang: lang_key = 'en'      
    else: lang_key = lang

    
    if val_key in translations:
        lang_map = translations[val_key]
        
        return lang_map.get(lang_key, lang_map.get('en', value.title()))

    
    return str(value).title()




@register.filter
def ruby_smart(kanji_text, kana_text):
    if not kanji_text or not kana_text:
        return kanji_text

    if re.fullmatch(r'[ぁ-んァ-ヶー\s、。！？（）]+', kanji_text):
        return kanji_text

    if kanji_text == kana_text:
        return kanji_text

    prefix_len = 0
    while (prefix_len < len(kanji_text) and prefix_len < len(kana_text) and 
           kanji_text[prefix_len] == kana_text[prefix_len]):
        prefix_len += 1
    
    suffix_len = 0
    while (suffix_len < (len(kanji_text) - prefix_len) and 
           suffix_len < (len(kana_text) - prefix_len) and 
           kanji_text[-(suffix_len + 1)] == kana_text[-(suffix_len + 1)]):
        suffix_len += 1
        
    prefix = kanji_text[:prefix_len]
    suffix = kanji_text[len(kanji_text)-suffix_len:] if suffix_len > 0 else ""
    mid_kanji = kanji_text[prefix_len:len(kanji_text)-suffix_len]
    mid_kana = kana_text[prefix_len:len(kana_text)-suffix_len]

    if not mid_kanji:
        return kanji_text

    match = re.search(r'[^一-龠]+', mid_kanji)
    if match:
        anchor = match.group()
        idx_k = mid_kanji.find(anchor)
        idx_kana = mid_kana.find(anchor)
        
        if idx_kana != -1:
            left = ruby_smart(mid_kanji[:idx_k], mid_kana[:idx_kana])
            right = ruby_smart(mid_kanji[idx_k + len(anchor):], mid_kana[idx_kana + len(anchor):])
            return mark_safe(f"{prefix}{left}{anchor}{right}{suffix}")

    return mark_safe(f'{prefix}<ruby>{mid_kanji}<rt style="font-size: 0.5em;">{mid_kana}</rt></ruby>{suffix}')