from django import template

from django.utils.safestring import mark_safe
import re

register = template.Library()


@register.filter
def game_reading(value):
    """A reading (name / romaji) as the game shows it: brackets and their
    contents removed. See thank_japan_app/game_display.py."""
    from thank_japan_app.game_display import strip_brackets
    return strip_brackets(value)


@register.filter
def game_meaning_of(word, field='englishname'):
    """One meaning column of a word as the game shows it (reading
    brackets handled). See thank_japan_app/game_display.py."""
    from thank_japan_app.game_display import word_game_meaning
    return word_game_meaning(word, field)

@register.filter
def format_category(value, lang_code='en'):
    if not value:
        return value

    
    val_key = str(value).lower().replace(" ", "")

    
    translations = {
        'culture': {'ja': '文化の言葉、いろいろ', 'en': 'Culture Words & More', 'de': 'Wörter rund um Kultur', 'fr': 'Mots de culture, en vrac', 'es': 'Palabras de cultura y más', 'it': 'Parole di cultura e altro', 'ko': '문화 관련 단어 모음', 'zh-hans': '各种文化词汇', 'zh-hant': '各種文化詞彙', 'vi': 'Các từ về văn hóa', 'th': 'คำศัพท์เกี่ยวกับวัฒนธรรมมากมาย', 'pt': 'Palavras de cultura e mais'},
        'food': {'ja': '食べ物、日本語でなんて言う?', 'en': 'How Do You Say "Food" in Japanese?', 'de': 'Wie sagt man „Essen" auf Japanisch?', 'fr': 'Comment dit-on « nourriture » en japonais ?', 'es': '¿Cómo se dice "comida" en japonés?', 'it': 'Come si dice "cibo" in giapponese?', 'ko': '"음식", 일본어로 뭐라고 할까?', 'zh-hans': '"食物"用日语怎么说?', 'zh-hant': '「食物」日語怎麼說?', 'vi': '"Thức ăn" tiếng Nhật nói thế nào?', 'th': '"อาหาร" ภาษาญี่ปุ่นพูดว่าอย่างไร?', 'pt': 'Como se diz "comida" em japonês?'},
        'cook': {'ja': '料理の言葉、いろいろ', 'en': 'Cooking Words & More', 'de': 'Wörter rund ums Kochen', 'fr': 'Mots de cuisine, en vrac', 'es': 'Palabras de cocina y más', 'it': 'Parole di cucina e altro', 'ko': '요리 관련 단어 모음', 'zh-hans': '各种烹饪词汇', 'zh-hant': '各種烹飪詞彙', 'vi': 'Các từ về nấu ăn', 'th': 'คำศัพท์เกี่ยวกับการทำอาหารมากมาย', 'pt': 'Palavras de culinária e mais'},
        'fashion': {'ja': 'ファッション、日本語でなんて言う?', 'en': 'How Do You Say "Fashion" in Japanese?', 'de': 'Wie sagt man „Mode" auf Japanisch?', 'fr': 'Comment dit-on « mode » en japonais ?', 'es': '¿Cómo se dice "moda" en japonés?', 'it': 'Come si dice "moda" in giapponese?', 'ko': '"패션", 일본어로 뭐라고 할까?', 'zh-hans': '"时尚"用日语怎么说?', 'zh-hant': '「時尚」日語怎麼說?', 'vi': '"Thời trang" tiếng Nhật nói thế nào?', 'th': '"แฟชั่น" ภาษาญี่ปุ่นพูดว่าอย่างไร?', 'pt': 'Como se diz "moda" em japonês?'},
        'nature': {'ja': '自然の言葉、いろいろ', 'en': 'Nature Words & More', 'de': 'Wörter rund um die Natur', 'fr': 'Mots de nature, en vrac', 'es': 'Palabras de naturaleza y más', 'it': 'Parole di natura e altro', 'ko': '자연 관련 단어 모음', 'zh-hans': '各种自然词汇', 'zh-hant': '各種自然詞彙', 'vi': 'Các từ về tự nhiên', 'th': 'คำศัพท์เกี่ยวกับธรรมชาติมากมาย', 'pt': 'Palavras de naturaleza e mais'},
        'animal': {'ja': '動物の名前、日本語で', 'en': 'Animal Names in Japanese', 'de': 'Tiernamen auf Japanisch', 'fr': "Noms d'animaux en japonais", 'es': 'Nombres de animales en japonés', 'it': 'Nomi di animali in giapponese', 'ko': '동물 이름, 일본어로', 'zh-hans': '日语中的动物名称', 'zh-hant': '日語中的動物名稱', 'vi': 'Tên các loài động vật trong tiếng Nhật', 'th': 'ชื่อสัตว์ในภาษาญี่ปุ่น', 'pt': 'Nomes de animais em japonês'},
        'sports': {'ja': 'スポーツ、日本語でなんて言う?', 'en': 'How Do You Say "Sports" in Japanese?', 'de': 'Wie sagt man „Sport" auf Japanisch?', 'fr': 'Comment dit-on « sport » en japonais ?', 'es': '¿Cómo se dice "deporte" en japonés?', 'it': 'Come si dice "sport" in giapponese?', 'ko': '"스포츠", 일본어로 뭐라고 할까?', 'zh-hans': '"运动"用日语怎么说?', 'zh-hant': '「運動」日語怎麼說?', 'vi': '"Thể thao" tiếng Nhật nói thế nào?', 'th': '"กีฬา" ภาษาญี่ปุ่นพูดว่าอย่างไร?', 'pt': 'Como se diz "esporte" em japonês?'},
        'householditems': {'ja': '家庭用品、日本語でなんて言う?', 'en': 'How Do You Say "Household Items" in Japanese?', 'de': 'Wie sagt man „Haushaltsgegenstände" auf Japanisch?', 'fr': 'Comment dit-on « articles ménagers » en japonais ?', 'es': '¿Cómo se dice "artículos del hogar" en japonés?', 'it': 'Come si dice "articoli per la casa" in giapponese?', 'ko': '"가정용품", 일본어로 뭐라고 할까?', 'zh-hans': '"家庭用品"用日语怎么说?', 'zh-hant': '「家庭用品」日語怎麼說?', 'vi': '"Đồ dùng gia đình" tiếng Nhật nói thế nào?', 'th': '"ของใช้ในครัวเรือน" ภาษาญี่ปุ่นพูดว่าอย่างไร?', 'pt': 'Como se diz "itens domésticos" em japonês?'},
        'appliances': {'ja': '家電、日本語でなんて言う?', 'en': 'How Do You Say "Appliances" in Japanese?', 'de': 'Wie sagt man „Haushaltsgeräte" auf Japanisch?', 'fr': 'Comment dit-on « appareils électroménagers » en japonais ?', 'es': '¿Cómo se dice "electrodomésticos" en japonés?', 'it': 'Come si dice "elettrodomestici" in giapponese?', 'ko': '"가전제품", 일본어로 뭐라고 할까?', 'zh-hans': '"家电"用日语怎么说?', 'zh-hant': '「家電」日語怎麼說?', 'vi': '"Thiết bị điện gia dụng" tiếng Nhật nói thế nào?', 'th': '"เครื่องใช้ไฟฟ้า" ภาษาญี่ปุ่นพูดว่าอย่างไร?', 'pt': 'Como se diz "eletrodomésticos" em japonês?'},
        'building': {'ja': '建物の言葉、いろいろ', 'en': 'Building Words & More', 'de': 'Wörter rund um Gebäude', 'fr': 'Mots de bâtiments, en vrac', 'es': 'Palabras de edificios y más', 'it': 'Parole di edifici e altro', 'ko': '건물 관련 단어 모음', 'zh-hans': '各种建筑词汇', 'zh-hant': '各種建築詞彙', 'vi': 'Các từ về tòa nhà', 'th': 'คำศัพท์เกี่ยวกับอาคารมากมาย', 'pt': 'Palavras de construções e mais'},
        'flower': {'ja': '花の名前、日本語で', 'en': 'Flower Names in Japanese', 'de': 'Blumennamen auf Japanisch', 'fr': 'Noms de fleurs en japonais', 'es': 'Nombres de flores en japonés', 'it': 'Nomi di fiori in giapponese', 'ko': '꽃 이름, 일본어로', 'zh-hans': '日语中的花卉名称', 'zh-hant': '日語中的花卉名稱', 'vi': 'Tên các loài hoa trong tiếng Nhật', 'th': 'ชื่อดอกไม้ในภาษาญี่ปุ่น', 'pt': 'Nomes de flores em japonês'},
        'work': {'ja': '仕事の言葉、いろいろ', 'en': 'Work Words & More', 'de': 'Wörter rund um die Arbeit', 'fr': 'Mots de travail, en vrac', 'es': 'Palabras de trabajo y más', 'it': 'Parole di lavoro e altro', 'ko': '일 관련 단어 모음', 'zh-hans': '各种工作词汇', 'zh-hant': '各種工作詞彙', 'vi': 'Các từ về công việc', 'th': 'คำศัพท์เกี่ยวกับงานมากมาย', 'pt': 'Palavras de trabalho e mais'},
        'live': {'ja': '生活の言葉、いろいろ', 'en': 'Everyday Life Words & More', 'de': 'Wörter rund um den Alltag', 'fr': 'Mots du quotidien, en vrac', 'es': 'Palabras de la vida cotidiana y más', 'it': 'Parole della vita quotidiana e altro', 'ko': '생활 관련 단어 모음', 'zh-hans': '各种生活词汇', 'zh-hant': '各種生活詞彙', 'vi': 'Các từ về cuộc sống', 'th': 'คำศัพท์เกี่ยวกับชีวิตประจำวันมากมาย', 'pt': 'Palavras do dia a dia e mais'},
        'body': {'ja': '体の部位、日本語で', 'en': 'Body Parts in Japanese', 'de': 'Körperteile auf Japanisch', 'fr': 'Parties du corps en japonais', 'es': 'Partes del cuerpo en japonés', 'it': 'Parti del corpo in giapponese', 'ko': '신체 부위, 일본어로', 'zh-hans': '日语中的身体部位', 'zh-hant': '日語中的身體部位', 'vi': 'Các bộ phận cơ thể trong tiếng Nhật', 'th': 'ส่วนต่าง ๆ ของร่างกายในภาษาญี่ปุ่น', 'pt': 'Partes do corpo em japonês'},
        'dailyactions': {'ja': '日常の動作、日本語でなんて言う?', 'en': 'How Do You Say Daily Actions in Japanese?', 'de': 'Wie sagt man Alltagshandlungen auf Japanisch?', 'fr': 'Comment dit-on les actions quotidiennes en japonais ?', 'es': '¿Cómo se dicen las acciones diarias en japonés?', 'it': 'Come si dicono le azioni quotidiane in giapponese?', 'ko': '일상적인 동작, 일본어로 뭐라고 할까?', 'zh-hans': '日常动作用日语怎么说?', 'zh-hant': '日常動作日語怎麼說?', 'vi': 'Hành động hàng ngày tiếng Nhật nói thế nào?', 'th': 'การกระทำในชีวิตประจำวันภาษาญี่ปุ่นพูดว่าอย่างไร?', 'pt': 'Como se dizem as ações diárias em japonês?'},
        'dailyconversation': {'ja': '日常会話、日本語でなんて言う?', 'en': 'How Do You Say Everyday Phrases in Japanese?', 'de': 'Wie sagt man Alltagsgespräche auf Japanisch?', 'fr': 'Comment dit-on les conversations du quotidien en japonais ?', 'es': '¿Cómo se dicen las conversaciones diarias en japonés?', 'it': 'Come si dicono le conversazioni quotidiane in giapponese?', 'ko': '일상 대화, 일본어로 뭐라고 할까?', 'zh-hans': '日常对话用日语怎么说?', 'zh-hant': '日常對話日語怎麼說?', 'vi': 'Hội thoại hàng ngày tiếng Nhật nói thế nào?', 'th': 'บทสนทนาประจำวันภาษาญี่ปุ่นพูดว่าอย่างไร?', 'pt': 'Como se dizem as conversas do dia a dia em japonês?'},
        'businessjapanese': {'ja': 'ビジネスの言葉、日本語で', 'en': 'Business Words in Japanese', 'de': 'Business-Wörter auf Japanisch', 'fr': "Mots d'affaires en japonais", 'es': 'Palabras de negocios en japonés', 'it': 'Parole di lavoro in giapponese', 'ko': '비즈니스 단어, 일본어로', 'zh-hans': '日语中的商务用语', 'zh-hant': '日語中的商務用語', 'vi': 'Từ vựng kinh doanh trong tiếng Nhật', 'th': 'คำศัพท์ธุรกิจในภาษาญี่ปุ่น', 'pt': 'Palavras de negócios em japonês'},
        'livinginjapan': {'ja': '日本の生活、言葉いろいろ', 'en': 'Life in Japan, Word by Word', 'de': 'Leben in Japan – Wort für Wort', 'fr': 'La vie au Japon, mot par mot', 'es': 'La vida en Japón, palabra por palabra', 'it': 'La vita in Giappone, parola per parola', 'ko': '일본 생활, 단어로 알아보기', 'zh-hans': '日本生活相关词汇', 'zh-hant': '日本生活相關詞彙', 'vi': 'Từ vựng về cuộc sống ở Nhật Bản', 'th': 'คำศัพท์เกี่ยวกับชีวิตในญี่ปุ่น', 'pt': 'A vida no Japão, palavra por palavra'},
        'medicalemergency': {'ja': '医療・救急の言葉、日本語で', 'en': 'Medical & Emergency Words in Japanese', 'de': 'Medizin- und Notfallwörter auf Japanisch', 'fr': "Mots médicaux et d'urgence en japonais", 'es': 'Palabras médicas y de emergencia en japonés', 'it': 'Parole mediche e di emergenza in giapponese', 'ko': '의료·응급 단어, 일본어로', 'zh-hans': '日语中的医疗急救用语', 'zh-hant': '日語中的醫療急救用語', 'vi': 'Từ vựng y tế & cấp cứu trong tiếng Nhật', 'th': 'คำศัพท์การแพทย์และฉุกเฉินในภาษาญี่ปุ่น', 'pt': 'Palavras médicas e de emergência em japonês'},
        'realestaterules': {'ja': '不動産の言葉、日本語で', 'en': 'Real Estate Words in Japanese', 'de': 'Immobilien-Wörter auf Japanisch', 'fr': "Mots de l'immobilier en japonais", 'es': 'Palabras inmobiliarias en japonés', 'it': 'Parole immobiliari in giapponese', 'ko': '부동산 단어, 일본어로', 'zh-hans': '日语中的房地产用语', 'zh-hant': '日語中的房地產用語', 'vi': 'Từ vựng bất động sản trong tiếng Nhật', 'th': 'คำศัพท์อสังหาริมทรัพย์ในภาษาญี่ปุ่น', 'pt': 'Palavras imobiliárias em japonês'},
        'tourismetiquette': {'ja': '観光マナー、日本語でなんて言う?', 'en': 'How Do You Say Tourism Etiquette in Japanese?', 'de': 'Wie sagt man Reise-Etikette auf Japanisch?', 'fr': "Comment dit-on l'étiquette touristique en japonais ?", 'es': '¿Cómo se dice la etiqueta turística en japonés?', 'it': 'Come si dice il galateo turistico in giapponese?', 'ko': '관광 매너, 일본어로 뭐라고 할까?', 'zh-hans': '旅游礼仪用日语怎么说?', 'zh-hant': '旅遊禮儀日語怎麼說?', 'vi': 'Nghi thức du lịch tiếng Nhật nói thế nào?', 'th': 'มารยาทการท่องเที่ยวภาษาญี่ปุ่นพูดว่าอย่างไร?', 'pt': 'Como se diz a etiqueta turística em japonês?'},
        'prefectures': {'ja': '都道府県の名前、日本語で', 'en': 'Prefecture Names in Japanese', 'de': 'Präfekturnamen auf Japanisch', 'fr': 'Noms des préfectures en japonais', 'es': 'Nombres de las prefecturas en japonés', 'it': 'Nomi delle prefetture in giapponese', 'ko': '도도부현 이름, 일본어로', 'zh-hans': '日语中的都道府县名称', 'zh-hant': '日語中的都道府縣名稱', 'vi': 'Tên các tỉnh thành trong tiếng Nhật', 'th': 'ชื่อจังหวัดในภาษาญี่ปุ่น', 'pt': 'Nomes das províncias em japonês'},
        'entertainment': {'ja': '娯楽の言葉、いろいろ', 'en': 'Entertainment Words & More', 'de': 'Wörter rund um Unterhaltung', 'fr': 'Mots de divertissement, en vrac', 'es': 'Palabras de entretenimiento y más', 'it': 'Parole di intrattenimento e altro', 'ko': '엔터테인먼트 관련 단어 모음', 'zh-hans': '各种娱乐词汇', 'zh-hant': '各種娛樂詞彙', 'vi': 'Các từ về giải trí', 'th': 'คำศัพท์เกี่ยวกับความบันเทิงมากมาย', 'pt': 'Palavras de entretenimento e mais'},
        'slang': {'ja': 'スラング、日本語でなんて言う?', 'en': 'How Do You Say Slang in Japanese?', 'de': 'Wie sagt man Slang auf Japanisch?', 'fr': "Comment dit-on l'argot en japonais ?", 'es': '¿Cómo se dice el argot en japonés?', 'it': 'Come si dice lo slang in giapponese?', 'ko': '슬랭, 일본어로 뭐라고 할까?', 'zh-hans': '俚语用日语怎么说?', 'zh-hant': '俚語日語怎麼說?', 'vi': 'Tiếng lóng tiếng Nhật nói thế nào?', 'th': 'สแลงภาษาญี่ปุ่นพูดว่าอย่างไร?', 'pt': 'Como se diz gírias em japonês?'},
        'romancerelationships': {'ja': '恋愛・人間関係の言葉、いろいろ', 'en': 'Romance & Relationship Words & More', 'de': 'Wörter rund um Liebe & Beziehungen', 'fr': "Mots d'amour et de relations, en vrac", 'es': 'Palabras de amor y relaciones y más', 'it': 'Parole di amore e relazioni e altro', 'ko': '연애·인간관계 관련 단어 모음', 'zh-hans': '各种恋爱与人际关系词汇', 'zh-hant': '各種戀愛與人際關係詞彙', 'vi': 'Các từ về tình yêu & mối quan hệ', 'th': 'คำศัพท์เกี่ยวกับความรักและความสัมพันธ์มากมาย', 'pt': 'Palavras de amor e relacionamentos e mais'},
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

    def to_h(text):
        return "".join(chr(ord(c) - 96) if 0x30A1 <= ord(c) <= 0x30F6 else c for c in text)
    
    comp_kanji = to_h(kanji_text)
    comp_kana = to_h(kana_text)

    if comp_kanji == comp_kana:
        return kanji_text
  
    prefix_len = 0
    while (prefix_len < len(kanji_text) and prefix_len < len(kana_text) and 
           comp_kanji[prefix_len] == comp_kana[prefix_len]):
        prefix_len += 1
    
    suffix_len = 0
    while (suffix_len < (len(kanji_text) - prefix_len) and 
           suffix_len < (len(kana_text) - prefix_len) and 
           comp_kanji[-(suffix_len + 1)] == comp_kana[-(suffix_len + 1)]):
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
        
        search_kana = to_h(mid_kana)
        idx_kana = search_kana.find(to_h(anchor))
        if idx_k > 0 and idx_kana == 0:
            idx_kana = search_kana.find(to_h(anchor), 1)
        
        if idx_kana != -1:
            left = ruby_smart(mid_kanji[:idx_k], mid_kana[:idx_kana])
            right = ruby_smart(mid_kanji[idx_k + len(anchor):], mid_kana[idx_kana + len(anchor):])
            return mark_safe(f"{prefix}{left}{anchor}{right}{suffix}")

    
    return mark_safe(f'{prefix}<ruby>{mid_kanji}<rt>{mid_kana}</rt></ruby>{suffix}')