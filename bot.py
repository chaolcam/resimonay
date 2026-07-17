import telebot
import os
import time
import datetime
from threading import Thread
from flask import Flask
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import pymongo

# --- AYARLAR VE GİZLİ KEYLER ---
TOKEN = os.environ.get("BOT_TOKEN") 
MONGO_URI = os.environ.get("MONGO_URI") 

ADMIN_GROUP_ID = -1003791676374
TARGET_CHANNEL_ID = -1003977263609 
CHANNEL_USERNAME = "yorumlapuanla" 

PATRON_ID = 7075582251

bot = telebot.TeleBot(TOKEN)

# --- MONGODB BAĞLANTISI ---
db_client = pymongo.MongoClient(MONGO_URI)
db = db_client["oylama_botu_veritabani"]
votes_col = db["oylar_kanal"] 
users_col = db["aboneler"]
bans_col = db["yasaklananlar"]
spam_col = db["spam_korumasi"] 
monthly_polls_col = db["ayin_birincisi_oylamalari"]

processed_albums = set()

# --- SESSİZ KAYIT SİSTEMİ ---
def kullanici_kaydet(user_id):
    if not user_id: return
    try:
        uid = int(user_id)
        if uid > 0 and "PATRON" not in str(user_id):
            users_col.update_one({"user_id": uid}, {"$set": {"user_id": uid}}, upsert=True)
    except:
        pass

# --- HAYALET HASAT MODU (GEÇMİŞİ KURTARMA) ---
def gecmisi_hasat_et():
    print("⏳ Eski oylardan kullanıcı kimlikleri hasat ediliyor...")
    sayac = 0
    try:
        all_votes = votes_col.find({})
        for doc in all_votes:
            voters = doc.get("voters", {})
            for v_id in voters.keys():
                if "PATRON" not in str(v_id):
                    try:
                        uid = int(v_id)
                        if uid > 0:
                            res = users_col.update_one({"user_id": uid}, {"$set": {"user_id": uid}}, upsert=True)
                            if res.upserted_id:
                                sayac += 1
                    except:
                        pass
        print(f"✅ Hasat Tamamlandı! Eski kayıtlardan '{sayac}' yeni aktif kullanıcı kurtarıldı.")
    except Exception as e:
        print(f"Hasat hatası: {e}")

# --- GELİŞMİŞ TOPLU MESAJ MOTORU (ANTI-ÇÖKME) ---
def toplu_mesaj_gonder(metin):
    aboneler = users_col.find({})
    basarili = 0
    engellemis = 0
    for abo in aboneler:
        u_id = abo.get("user_id")
        if not u_id: continue
        try:
            bot.send_message(u_id, metin, parse_mode="HTML")
            basarili += 1
            time.sleep(0.05) 
        except Exception as e:
            if "blocked" in str(e) or "deactivated" in str(e) or "chat not found" in str(e):
                users_col.delete_one({"user_id": u_id})
                engellemis += 1
    print(f"📢 Yayın Raporu -> Başarılı: {basarili} | Engelleyenler Temizlendi: {engellemis}")
    return basarili, engellemis

# --- HAFTANIN BİRİNCİSİ ---
def sec_haftanin_birincisi():
    all_votes = votes_col.find({})
    simdi = datetime.datetime.utcnow()
    gecen_hafta = simdi - datetime.timedelta(days=7)
    
    ranking_data = []
    for doc in all_votes:
        created_at = doc.get("created_at")
        if created_at is None:
            pass 
        elif created_at < gecen_hafta:
            continue
            
        if doc.get("is_weekly_winner"):
            continue
            
        voters = doc.get("voters", {})
        if not voters: continue
        total_votes = len(voters)
        avg_score = sum(voters.values()) / total_votes
        
        if total_votes >= 1: 
            bayesian_score = (total_votes * avg_score + 20 * 6.0) / (total_votes + 20)
            ranking_data.append({"msg_id": doc.get("msg_id"), "avg_score": avg_score, "total_votes": total_votes, "_id": doc["_id"], "bayesian_score": bayesian_score})
            
    if not ranking_data: return
        
    ranking_data.sort(key=lambda x: (x["bayesian_score"], x["total_votes"]), reverse=True)
    winner = ranking_data[0]
    votes_col.update_one({"_id": winner["_id"]}, {"$set": {"is_weekly_winner": True}})
    
    link = f"https://t.me/{CHANNEL_USERNAME}/{winner['msg_id']}"
    metin = f"🏆 <b>HAFTANIN BİRİNCİSİ</b> 🏆\n\n⭐ Güven Puanı: {winner['bayesian_score']:.2f} <i>({winner['total_votes']} oy)</i>\n\nBu muhteşem gönderiyi tekrar görmek için tıklayın: {link}\n\n👇 <i>Sen de fotoğrafını oylatmak istiyorsan @resimonaybot'a mesaj gönderebilirsin!</i>"
    
    try:
        sent_msg = bot.copy_message(TARGET_CHANNEL_ID, TARGET_CHANNEL_ID, winner['msg_id'], caption=metin, parse_mode="HTML")
        bot.pin_chat_message(chat_id=TARGET_CHANNEL_ID, message_id=sent_msg.message_id)
    except Exception as e:
        print("Mesaj atma veya sabitleme hatası:", e)

def is_monthly_poll_day(date_obj):
    if date_obj.weekday() != 0: return False
    dun = date_obj - datetime.timedelta(days=1)
    haftaya = dun + datetime.timedelta(days=7)
    return haftaya.month != dun.month

aylar = {
    1: "OCAK", 2: "ŞUBAT", 3: "MART",
    4: "NİSAN", 5: "MAYIS", 6: "HAZİRAN",
    7: "TEMMUZ", 8: "AĞUSTOS", 9: "EYLÜL",
    10: "EKİM", 11: "KASIM", 12: "ARALIK"
}

def baslat_ayin_birincisi_oylamasi():
    simdi = datetime.datetime.utcnow()
    
    sampiyonlar = list(votes_col.find({
        "is_weekly_winner": True,
        "is_in_monthly_poll": {"$ne": True},
        "created_at": {"$lte": simdi}
    }).sort([("created_at", 1)]).limit(5))
    
    if len(sampiyonlar) < 2:
        try: bot.send_message(ADMIN_GROUP_ID, "⚠️ Aylık şampiyonlar ligi başlatılamadı çünkü henüz oylanmamış yeterli aday bulunamadı.")
        except: pass
        return
        
    ay_ismi = aylar.get(simdi.month, "BU AY")
    ay_ismi_kucuk = ay_ismi.lower()
    aday_sayisi = len(sampiyonlar)
    
    for doc in sampiyonlar:
        votes_col.update_one({"_id": doc["_id"]}, {"$set": {"is_in_monthly_poll": True}})
        
    poll_id = f"poll_{simdi.timestamp()}"
    SURE_METNI = "1 Gün"
    
    duyuru_metni = f"🏆 <b>{ay_ismi} AYININ ŞAMPİYONLAR LİGİ BAŞLADI!</b> 🏆\n\nKanalın {ay_ismi_kucuk} ayında gelmiş geçmiş en iyi Top {aday_sayisi} fotoğrafı kapışıyor. Yalnızca <b>1 adaya</b> oy verebilirsiniz. \n⏱️ Oylama {SURE_METNI} sürecek!\n\n👇 <i>Favori şampiyonuna oyunu ver!</i>"
    try: 
        sent_duyuru = bot.send_message(TARGET_CHANNEL_ID, duyuru_metni, parse_mode="HTML")
        duyuru_msg_id = sent_duyuru.message_id
        try: bot.pin_chat_message(TARGET_CHANNEL_ID, duyuru_msg_id)
        except: pass
    except Exception as e: 
        try: bot.send_message(ADMIN_GROUP_ID, f"Duyuru atılamadı: {e}")
        except: pass
        return
    
    channel_msg_ids = []
    for i, doc in enumerate(sampiyonlar):
        orig_msg_id = doc["msg_id"]
        aday_duyuru = f"👑 <a href='https://t.me/{CHANNEL_USERNAME}/{orig_msg_id}'><b>Aday {i+1}</b></a>\n👇 Bu adaya oy vermek için aşağıdaki butona tıklayın!"
        markup = InlineKeyboardMarkup()
        btn = InlineKeyboardButton(f"👑 Bu 1. Olsun (0 Oy)", callback_data=f"mpoll_{poll_id}_{orig_msg_id}")
        markup.add(btn)
        try:
            sent = bot.copy_message(TARGET_CHANNEL_ID, TARGET_CHANNEL_ID, orig_msg_id, caption=aday_duyuru, parse_mode="HTML", reply_markup=markup)
            channel_msg_ids.append({"orijinal_msg_id": orig_msg_id, "yeni_msg_id": sent.message_id})
        except: pass
            
    try:
        log_msg = bot.send_message(ADMIN_GROUP_ID, "📊 <b>Aylık Şampiyonlar Ligi Oylama Durumu</b>\n\n<i>Henüz oy kullanılmadı.</i>", parse_mode="HTML")
        log_msg_id = log_msg.message_id
    except:
        log_msg_id = None
        
    monthly_polls_col.update_one(
        {"poll_id": poll_id},
        {"$set": {
            "poll_id": poll_id,
            "duyuru_msg_id": duyuru_msg_id,
            "log_msg_id": log_msg_id,
            "candidates": channel_msg_ids,
            "votes": {},
            "voter_names": {},
            "active": True,
            "created_at": simdi
        }},
        upsert=True
    )

def bitir_ayin_birincisi_oylamasi():
    simdi = datetime.datetime.utcnow()
    poll = monthly_polls_col.find_one({"active": True})
    if not poll: return
    poll_id = poll["poll_id"]
    
    votes = poll.get("votes", {})
    oy_sayilari = {}
    for uid, aid in votes.items():
        oy_sayilari[aid] = oy_sayilari.get(aid, 0) + 1
        
    kazanan_aday_id = None
    kazanan_oy = -1
    for aid, adet in oy_sayilari.items():
        if adet > kazanan_oy:
            kazanan_oy = adet
            kazanan_aday_id = aid
            
    if kazanan_aday_id is None:
        if poll.get("candidates"):
            kazanan_aday_id = poll["candidates"][0]["orijinal_msg_id"]
            kazanan_oy = 0
        else:
            monthly_polls_col.update_one({"poll_id": poll_id}, {"$set": {"active": False}})
            return
            
    votes_col.update_one({"msg_id": kazanan_aday_id}, {"$set": {"is_monthly_winner": True}})
    
    duyuru = f"👑 <b>AYIN ŞAMPİYONU BELLİ OLDU!</b> 👑\n\nToplam <b>{kazanan_oy}</b> oy alarak bu ayın birincisi olan bu muhteşem hatunu ve gavatını tebrik ediyoruz!\n\n👇 <i>Sen de şampiyon olmak istiyorsan @resimonaybot'a fotoğraf at!</i>"
    try:
        sent = bot.copy_message(TARGET_CHANNEL_ID, TARGET_CHANNEL_ID, kazanan_aday_id, caption=duyuru, parse_mode="HTML")
        bot.pin_chat_message(chat_id=TARGET_CHANNEL_ID, message_id=sent.message_id)
    except: pass
        
    monthly_polls_col.update_one({"poll_id": poll_id}, {"$set": {"active": False}})
    
    candidates = poll.get("candidates", [])
    for cand in candidates:
        orig = cand["orijinal_msg_id"]
        yeni_msg_id = cand["yeni_msg_id"]
        oy_sayisi = oy_sayilari.get(orig, 0)
        markup = InlineKeyboardMarkup()
        if orig == kazanan_aday_id:
            buton_yazisi = f"🏆 ŞAMPİYON 🏆 ({oy_sayisi} Oy)"
        else:
            buton_yazisi = f"❌ Oylama Bitti ({oy_sayisi} Oy)"
        btn = InlineKeyboardButton(buton_yazisi, callback_data="none")
        markup.add(btn)
        try: bot.edit_message_reply_markup(chat_id=TARGET_CHANNEL_ID, message_id=yeni_msg_id, reply_markup=markup)
        except: pass

def otomatik_mesaj_dongusu():
    mesaj_atildi = False
    haftanin_birincisi_secildi = False
    ayin_birincisi_basladi = False
    ayin_birincisi_uyari = False
    ayin_birincisi_bitti = False
    print("⏰ Zamanlayıcı Motoru Çalıştırıldı...")
    while True:
        try:
            simdi_utc = datetime.datetime.utcnow()
            tr_saati = simdi_utc + datetime.timedelta(hours=3)
            
            # Aylık Şampiyonlar Ligi Mantığı (Pazartesi Günü)
            if is_monthly_poll_day(tr_saati):
                if tr_saati.hour == 0 and tr_saati.minute == 1:
                    if not ayin_birincisi_basladi:
                        baslat_ayin_birincisi_oylamasi()
                        ayin_birincisi_basladi = True
                
                if tr_saati.hour == 23 and tr_saati.minute == 58:
                    if not ayin_birincisi_uyari:
                        try: bot.send_message(TARGET_CHANNEL_ID, "⏱️ 1 günlük oylama süresi doldu, 1. seçiliyor bekleyin...")
                        except: pass
                        ayin_birincisi_uyari = True
                        
                if tr_saati.hour == 23 and tr_saati.minute == 59:
                    if not ayin_birincisi_bitti:
                        bitir_ayin_birincisi_oylamasi()
                        ayin_birincisi_bitti = True
            else:
                ayin_birincisi_basladi = False
                ayin_birincisi_uyari = False
                ayin_birincisi_bitti = False
            
            # Her gün 18:00
            if tr_saati.hour == 18 and tr_saati.minute == 0:
                if not mesaj_atildi:
                    uyari_metni = (
                        "⚠️ <b>Yasal Uyarı:</b> Burada paylaşılan medyalardaki kişilerin rızası ile atıldığı kabul edilir. "
                        "Doğabilecek olası yasal sorunlardan veya sorumluluklardan bot yönetimi sorumlu değildir."
                    )
                    toplu_mesaj_gonder(uyari_metni)
                    mesaj_atildi = True
            else:
                mesaj_atildi = False 
                
            # Her Pazar 23:59
            if tr_saati.weekday() == 6 and tr_saati.hour == 23 and tr_saati.minute == 59:
                if not haftanin_birincisi_secildi:
                    sec_haftanin_birincisi()
                    haftanin_birincisi_secildi = True
            else:
                haftanin_birincisi_secildi = False
                
            time.sleep(30)
        except Exception as e:
            time.sleep(30)

# --- YARDIMCI FONKSİYON: Puanlama Butonları ---
def generate_rating_keyboard(message_id):
    doc = votes_col.find_one({"msg_id": message_id})
    msg_votes = doc.get("voters", {}) if doc else {}
    counts = {i: 0 for i in range(1, 11)}
    for v in msg_votes.values():
        if v in counts:
            counts[v] += 1
            
    markup = InlineKeyboardMarkup(row_width=5)
    row_buttons = []
    for score in range(1, 11):
        text = f"{score}({counts[score]})"
        btn = InlineKeyboardButton(text, callback_data=f"r_{message_id}_{score}")
        row_buttons.append(btn)
    markup.add(*row_buttons)
    return markup

# 1. /start Komutu
@bot.message_handler(commands=['start'], chat_types=['private'])
def send_welcome(message):
    kullanici_kaydet(message.from_user.id)
    welcome_text = (
        "Hoş geldiniz! Bu bot sayesinde gönderdiğiniz resimleri/videoları oylatabilirsiniz. "
        "Lütfen sadece tek bir resim veya video gönderiniz. "
        "Gönderiniz admin onayından geçince kanalımızda paylaşılacaktır.\n\n"
        "⚠️ <b>Yasal Uyarı:</b> Burada paylaşılan medyalardaki kişilerin rızası ile atıldığı kabul edilir. "
        "Doğabilecek olası yasal sorunlardan veya sorumluluklardan bot yönetimi sorumlu değildir.\n\n"
        "🏆 Kanalın en iyilerini görmek için /siralama yazabilirsiniz!"
    )
    try: bot.reply_to(message, welcome_text, parse_mode="HTML")
    except: pass

# 2. Admin Manuel Toplu Mesaj Komutu (/mesaj)
@bot.message_handler(commands=['mesaj'])
def admin_manuel_mesaj(message):
    if message.chat.id != ADMIN_GROUP_ID: return
    parts = message.text.split(" ", 1)
    if len(parts) < 2:
        bot.reply_to(message, "⚠️ Metin girmeyi unuttunuz!\n\n<b>Kullanım:</b> <code>/mesaj İletilecek Mesaj Metni</code>", parse_mode="HTML")
        return
    yayin_metni = parts[1].strip()
    durum = bot.reply_to(message, "⏳ Toplu mesaj yayını başlatıldı, lütfen bekleyiniz...")
    basarili, engellemis = toplu_mesaj_gonder(yayin_metni)
    try:
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=durum.message_id,
            text=f"📢 <b>Yayın Tamamlandı!</b>\n\n✅ Ulaşan: <code>{basarili}</code> kullanıcı\n❌ Engelleyen/Silinen: <code>{engellemis}</code> kullanıcı (Veritabanından temizlendi)",
            parse_mode="HTML"
        )
    except: pass

# 3. /siralama Komutu
@bot.message_handler(commands=['siralama'])
def send_ranking(message):
    kullanici_kaydet(message.from_user.id)
    try:
        all_votes = votes_col.find({})
        ranking_data = []
        for doc in all_votes:
            msg_id = doc.get("msg_id")
            voters = doc.get("voters", {})
            if not voters: continue
            total_votes = len(voters)
            avg_score = sum(voters.values()) / total_votes
            if total_votes >= 1:
                bayesian_score = (total_votes * avg_score + 20 * 6.0) / (total_votes + 20)
                ranking_data.append({"msg_id": msg_id, "avg_score": avg_score, "total_votes": total_votes, "bayesian_score": bayesian_score})
        
        if not ranking_data:
            bot.reply_to(message, "Henüz hiç oy alan gönderi bulunmuyor.")
            return

        ranking_data.sort(key=lambda x: (x["bayesian_score"], x["total_votes"]), reverse=True)
        top_10 = ranking_data[:10]
        
        text = "🏆 <b>En Yüksek Puanlı Gönderiler (Top 10)</b> 🏆\n\n"
        for i, data in enumerate(top_10, 1):
            msg_id = data["msg_id"]
            avg = data["bayesian_score"]
            votes_count = data["total_votes"]
            link = f"https://t.me/{CHANNEL_USERNAME}/{msg_id}"
            text += f"<b>{i}.</b> <a href='{link}'>Gönderiye Git</a> - ⭐ {avg:.2f} <i>({votes_count} oy)</i>\n"
            
        bot.reply_to(message, text, parse_mode="HTML", disable_web_page_preview=True)
    except:
        bot.reply_to(message, "Sıralama oluşturulurken bir hata oluştu.")

# /sil Komutu
@bot.message_handler(commands=['sil'])
def delete_db_record(message):
    parts = message.text.split()
    if len(parts) != 2:
        bot.reply_to(message, "⚠️ Eksik komut girdiniz.\n\n<b>Kullanım:</b> /sil mesaj_id", parse_mode="HTML")
        return
    try:
        msg_id = int(parts[1])
        sonuc = votes_col.delete_one({"msg_id": msg_id})
        if sonuc.deleted_count > 0:
            bot.reply_to(message, f"✅ <b>Başarılı!</b> {msg_id} ID'li gönderi veritabanından silindi.", parse_mode="HTML")
        else:
            bot.reply_to(message, f"❌ Veritabanında kayıt bulunamadı.", parse_mode="HTML")
    except:
        pass

# Özel mesajdan gelen resimleri/videoları yakala
@bot.message_handler(content_types=['photo', 'video'], chat_types=['private'])
def handle_media(message):
    user_id = message.from_user.id
    
    # --- AYLIK OYLAMA KONTROLÜ ---
    aktif_poll = monthly_polls_col.find_one({"active": True})
    if aktif_poll:
        duyuru_msg_id = aktif_poll.get("duyuru_msg_id", "")
        kanal_ismi = CHANNEL_USERNAME
        link = f"https://t.me/{kanal_ismi}/{duyuru_msg_id}"
        
        bot.reply_to(message, f"🚫 <b>Şu an aylık şampiyonlar ligi oylaması aktif!</b>\n\nBugün yeni fotoğraf paylaşımı alınmayacaktır. Lütfen gidip aylık şampiyonumuz için oy verin:\n\n👉 {link}", parse_mode="HTML", disable_web_page_preview=True)
        return
        
    kullanici_kaydet(user_id) 
    
    # 1. Ban Kontrolü
    if user_id != PATRON_ID:
        is_banned = bans_col.find_one({"user_id": user_id})
        if is_banned:
            try: bot.reply_to(message, "🚫 <b>Yasaklandınız!</b> Botu kullanma hakkınız elinizden alınmıştır.", parse_mode="HTML")
            except: pass
            return
            
    # 2. Spam / Tekrar Kontrolü
    if message.content_type == 'photo':
        file_unique_id = message.photo[-1].file_unique_id
    else:
        file_unique_id = message.video.file_unique_id

    if user_id != PATRON_ID:
        spam_kaydi = spam_col.find_one({"file_unique_id": file_unique_id, "user_id": user_id})
        
        if spam_kaydi:
            deneme = spam_kaydi.get("attempts", 1)
            if deneme >= 3:
                try: bot.reply_to(message, "🚨 Aynı resmi defalarca göndermeyi denediğiniz için bu resim artık kabul edilmiyor! Lütfen spam yapmayın.")
                except: pass
                return
            else:
                spam_col.update_one({"_id": spam_kaydi["_id"]}, {"$inc": {"attempts": 1}})
                try: bot.reply_to(message, f"⚠️ Bu resmi daha önce göndermiştiniz (Deneme {deneme + 1}/3). Onay veya red bekliyor olabilir. Lütfen sonucu bekleyiniz.")
                except: pass
                return
        else:
            spam_col.insert_one({"file_unique_id": file_unique_id, "user_id": user_id, "attempts": 1})

    caption = message.caption if message.caption else ""
    user_name = str(message.from_user.first_name).replace("<", "").replace(">", "")
    orig_msg_id = message.message_id  

    if message.media_group_id:
        if message.media_group_id not in processed_albums:
            processed_albums.add(message.media_group_id)
            try: bot.reply_to(message, "⚠️ Lütfen medyaları albüm olarak değil, tek tek gönderiniz.")
            except: pass
        return

    user_link = f"@{message.from_user.username}" if message.from_user.username else f'<a href="tg://user?id={user_id}">{user_name}</a>'
    
    # 3. Butonları Ekleme (Onay, Red, Ban)
    markup = InlineKeyboardMarkup()
    btn_approve = InlineKeyboardButton("Onayla ✅", callback_data=f"approve_{user_id}_{orig_msg_id}")
    btn_reject = InlineKeyboardButton("Reddet ❌", callback_data=f"reject_{user_id}_{orig_msg_id}")
    btn_ban = InlineKeyboardButton("Kullanıcıyı Banla 🚫", callback_data=f"banuser_{user_id}_{orig_msg_id}")
    markup.add(btn_approve, btn_reject)
    markup.add(btn_ban)

    admin_text = f"<b>Gönderen:</b> {user_link}\n\n{caption}"
    try:
        if message.content_type == 'photo':
            bot.send_photo(ADMIN_GROUP_ID, message.photo[-1].file_id, caption=admin_text, reply_markup=markup, parse_mode='HTML')
        elif message.content_type == 'video':
            bot.send_video(ADMIN_GROUP_ID, message.video.file_id, caption=admin_text, reply_markup=markup, parse_mode='HTML')
        bot.reply_to(message, "Resminiz adminlerimize iletilmiştir, lütfen bekleyiniz.")
    except Exception as e:
        print(f"Hata: {e}")

# TARTIŞMA GRUBU YAKALAYICISI
@bot.message_handler(content_types=['photo', 'video', 'text'], func=lambda m: getattr(m, 'is_automatic_forward', False))
def handle_group_forwards(message):
    if message.forward_from_chat and message.forward_from_chat.id == TARGET_CHANNEL_ID:
        # SADECE VERİTABANINDA OLAN (BOTUN GÖNDERDİĞİ) MESAJLARA BUTON EKLE
        channel_msg_id = message.forward_from_message_id
        if not votes_col.find_one({"msg_id": channel_msg_id}):
            return
            
        group_msg_id = message.message_id
        group_chat_id = message.chat.id
        markup = generate_rating_keyboard(channel_msg_id)
        try:
            reply_text = "👇 Oylamaya bu tartışma grubundan da katılabilirsiniz 👇"
            reply_msg = bot.send_message(chat_id=group_chat_id, text=reply_text, reply_to_message_id=group_msg_id, reply_markup=markup)
            votes_col.update_one({"msg_id": channel_msg_id}, {"$set": {"group_reply_msg_id": reply_msg.message_id, "group_chat_id": group_chat_id}}, upsert=True)
        except:
            pass

@bot.callback_query_handler(func=lambda call: call.data == "none")
def handle_none_callback(call):
    bot.answer_callback_query(call.id, "Oylama Bitti! Maalesef oy veremezsiniz.")

@bot.callback_query_handler(func=lambda call: call.data.startswith("mpoll_"))
def handle_monthly_poll(call):
    data = call.data
    parts = data.split("_")
    poll_id = "_".join(parts[1:-1])
    aday_id = int(parts[-1])
    user_id = str(call.from_user.id)
    
    poll = monthly_polls_col.find_one({"poll_id": poll_id})
    if not poll or not poll.get("active"):
        bot.answer_callback_query(call.id, "Bu oylama artık aktif değil!")
        return
        
    votes = poll.get("votes", {})
    eski_oy = votes.get(user_id)
    if eski_oy == aday_id:
        bot.answer_callback_query(call.id, "Zaten bu adaya oy verdiniz!", show_alert=True)
        return
        
    voter_names = poll.get("voter_names", {})
    voter_names[user_id] = call.from_user.first_name
    
    votes[user_id] = aday_id
    monthly_polls_col.update_one({"poll_id": poll_id}, {"$set": {"votes": votes, "voter_names": voter_names}})
    
    bot.answer_callback_query(call.id, "Oyunuz başarıyla kaydedildi/değiştirildi!")
    
    oy_sayilari = {}
    aday_oy_verenler = {}
    for uid, aid in votes.items():
        oy_sayilari[aid] = oy_sayilari.get(aid, 0) + 1
        aday_oy_verenler.setdefault(aid, []).append(uid)
        
    # Admin grubundaki Log mesajını güncelle
    log_msg_id = poll.get("log_msg_id")
    if log_msg_id:
        log_text = "📊 <b>Aylık Şampiyonlar Ligi Oylama Durumu</b>\n\n"
        candidates = poll.get("candidates", [])
        kanal_ismi = CHANNEL_USERNAME
        
        for i, cand in enumerate(candidates):
            orig_id = cand["orijinal_msg_id"]
            yeni_msg_id = cand["yeni_msg_id"]
            verenler = aday_oy_verenler.get(orig_id, [])
            aday_link = f"https://t.me/{kanal_ismi}/{yeni_msg_id}"
            log_text += f"🏆 <a href='{aday_link}'><b>Aday {i+1}</b></a> ({len(verenler)} Oy):\n"
            
            for uid in verenler:
                isim = voter_names.get(uid, "Bilinmeyen").replace('<', '').replace('>', '')
                kullanici_link = f'<a href="tg://user?id={uid}">{isim}</a>'
                log_text += f" 👤 {kullanici_link}\n"
            log_text += "\n"
        
        try: bot.edit_message_text(chat_id=ADMIN_GROUP_ID, message_id=log_msg_id, text=log_text, parse_mode="HTML")
        except: pass
            
    etkilenen_adaylar = set([aday_id])
    if eski_oy: etkilenen_adaylar.add(eski_oy)
    
    candidates = poll.get("candidates", [])
    for cand in candidates:
        orig = cand["orijinal_msg_id"]
        if orig in etkilenen_adaylar:
            yeni_msg_id = cand["yeni_msg_id"]
            oy_sayisi = oy_sayilari.get(orig, 0)
            markup = InlineKeyboardMarkup()
            btn = InlineKeyboardButton(f"👑 Bu 1. Olsun ({oy_sayisi} Oy)", callback_data=f"mpoll_{poll_id}_{orig}")
            markup.add(btn)
            try: bot.edit_message_reply_markup(chat_id=TARGET_CHANNEL_ID, message_id=yeni_msg_id, reply_markup=markup)
            except: pass

# Buton tıklamalarını işleme
@bot.callback_query_handler(func=lambda call: not call.data.startswith("mpoll_") and call.data != "none")
def handle_callback(call):
    data = call.data
    
    # --- OYLAMA KISMI ---
    if data.startswith("r_"):
        try:
            _, msg_id_str, score_str = data.split("_")
            msg_id = int(msg_id_str)
            score = int(score_str)
            real_user_id = call.from_user.id
            kullanici_kaydet(real_user_id) 
            
            if real_user_id == PATRON_ID:
                voter_id = f"PATRON_{int(time.time() * 1000)}"
            else:
                voter_id = str(real_user_id)
            
            doc = votes_col.find_one({"msg_id": msg_id})
            msg_votes = doc.get("voters", {}) if doc else {}
            voter_names = doc.get("voter_names", {}) if doc else {}
            current_vote = msg_votes.get(voter_id)
            
            if real_user_id != PATRON_ID and current_vote == score:
                bot.answer_callback_query(call.id, f"Zaten bu resme {score} puan vermişsiniz!")
                return
            
            msg_votes[voter_id] = score
            if real_user_id == PATRON_ID:
                voter_names[voter_id] = "GHOST"
            else:
                isim = str(call.from_user.first_name).replace("<", "").replace(">", "")
                voter_names[voter_id] = f'<a href="tg://user?id={real_user_id}">{isim}</a>'
            
            votes_col.update_one({"msg_id": msg_id}, {"$set": {"voters": msg_votes, "voter_names": voter_names}}, upsert=True)
            total_votes = len(msg_votes)
            avg_score = sum(msg_votes.values()) / total_votes
            bayesian_score = (total_votes * avg_score + 20 * 6.0) / (total_votes + 20)
            
            try:
                full_caption = call.message.caption if call.message.caption else ""
                base_caption = full_caption.split("📊 Oylama Sonucu:")[0].strip() if "📊 Oylama Sonucu:" in full_caption else full_caption.strip()
                new_caption = f"{base_caption}\n\n📊 Oylama Sonucu:\n⭐ Güven Puanı: {bayesian_score:.2f} / 10 ({total_votes} oy)" if base_caption else f"📊 Oylama Sonucu:\n⭐ Güven Puanı: {bayesian_score:.2f} / 10 ({total_votes} oy)"
                new_markup = generate_rating_keyboard(msg_id)
                bot.edit_message_caption(chat_id=TARGET_CHANNEL_ID, message_id=msg_id, caption=new_caption, reply_markup=new_markup)
            except: pass 
                
            new_markup = generate_rating_keyboard(msg_id)
            if doc and "group_reply_msg_id" in doc and "group_chat_id" in doc:
                try:
                    group_text = f"👇 Oylamaya bu tartışma grubundan da katılabilirsiniz 👇\n\n📊 Oylama Sonucu:\n⭐ Güven Puanı: {bayesian_score:.2f} / 10 ({total_votes} oy)"
                    bot.edit_message_text(chat_id=doc["group_chat_id"], message_id=doc["group_reply_msg_id"], text=group_text, reply_markup=new_markup)
                except: pass

            if doc and "log_msg_id" in doc and "log_chat_id" in doc:
                log_text = f"📊 <b>Güncel Oylama Durumu (Toplam: {total_votes} Oy)</b>\n\n"
                has_visible_vote = False
                for v_id, v_score in msg_votes.items():
                    v_name = voter_names.get(v_id, "Bilinmeyen")
                    if v_name != "GHOST":
                        log_text += f"👤 {v_name}: {v_score} Puan\n"
                        has_visible_vote = True
                if not has_visible_vote: log_text += "<i>Henüz görünür bir oy yok...</i>"
                try: bot.edit_message_text(chat_id=doc["log_chat_id"], message_id=doc["log_msg_id"], text=log_text, parse_mode="HTML", disable_web_page_preview=True)
                except: pass

            bot.answer_callback_query(call.id, f"Başarılı: {score} puan verdiniz!")
        except:
            bot.answer_callback_query(call.id, "Bir hata oluştu.")
        return

    # --- ONAY / RED / BAN KISMI ---
    admin_msg = call.message
    parts = data.split("_")
    action = parts[0]
    user_id = parts[1]
    orig_msg_id = int(parts[2]) if len(parts) > 2 else None 
    
    kullanici_kaydet(user_id)
    
    plain_caption = admin_msg.caption if admin_msg.caption else ""
    original_caption = plain_caption.split("\n\n", 1)[1].strip() if "\n\n" in plain_caption else ""
    html_full_caption = admin_msg.html_caption if admin_msg.html_caption else plain_caption

    # Etiketi Hazırla
    admin_isim = str(call.from_user.first_name).replace('<', '').replace('>', '')
    admin_etiket = f'<a href="tg://user?id={call.from_user.id}">{admin_isim}</a>'

    if action == "approve":
        try:
            sent_msg = None
            initial_caption = f"{original_caption}\n\n📊 Oylama Sonucu:\n⭐ Henüz oy verilmedi." if original_caption else "📊 Oylama Sonucu:\n⭐ Henüz oy verilmedi."
            if admin_msg.content_type == 'photo':
                sent_msg = bot.send_photo(TARGET_CHANNEL_ID, admin_msg.photo[-1].file_id, caption=initial_caption)
            elif admin_msg.content_type == 'video':
                sent_msg = bot.send_video(TARGET_CHANNEL_ID, admin_msg.video.file_id, caption=initial_caption)
            
            post_link = ""
            if sent_msg:
                log_msg = bot.send_message(chat_id=admin_msg.chat.id, text="📊 <b>Güncel Oylama Durumu</b>\n<i>Henüz oy verilmedi...</i>", reply_to_message_id=admin_msg.message_id, parse_mode="HTML")
                votes_col.insert_one({"msg_id": sent_msg.message_id, "voters": {}, "voter_names": {}, "log_msg_id": log_msg.message_id, "log_chat_id": admin_msg.chat.id, "created_at": datetime.datetime.utcnow()})
                initial_markup = generate_rating_keyboard(sent_msg.message_id)
                bot.edit_message_reply_markup(chat_id=TARGET_CHANNEL_ID, message_id=sent_msg.message_id, reply_markup=initial_markup)
                post_link = f"https://t.me/{CHANNEL_USERNAME}/{sent_msg.message_id}"

            yeni_baslik = f"✅ {admin_etiket} Tarafından ONAYLANDI\n\n{html_full_caption}"
            if len(yeni_baslik) > 1024:
                yeni_baslik = yeni_baslik[:1020] + "..."
                
            try: bot.edit_message_caption(yeni_baslik, chat_id=admin_msg.chat.id, message_id=admin_msg.message_id, reply_markup=None, parse_mode='HTML')
            except: 
                try: bot.edit_message_reply_markup(chat_id=admin_msg.chat.id, message_id=admin_msg.message_id, reply_markup=None)
                except: pass

            try: 
                bildirim_mesaji = f"🎉 Resminiz onaylandı ve kanalımızda paylaşıldı!\n\nBuradaki linkten ulaşabilirsiniz:\n{post_link}"
                if orig_msg_id: bot.send_message(user_id, bildirim_mesaji, reply_to_message_id=orig_msg_id)
                else: bot.send_message(user_id, bildirim_mesaji)
            except: pass
            
            bot.answer_callback_query(call.id, "İçerik kanalda paylaşıldı!")
        except Exception as e:
            bot.answer_callback_query(call.id, f"Hata oluştu: {str(e)[:50]}")

    elif action == "reject":
        try:
            yeni_baslik = f"❌ {admin_etiket} Tarafından REDDEDİLDİ\n\n{html_full_caption}"
            if len(yeni_baslik) > 1024:
                yeni_baslik = yeni_baslik[:1020] + "..."
                
            try: bot.edit_message_caption(yeni_baslik, chat_id=admin_msg.chat.id, message_id=admin_msg.message_id, reply_markup=None, parse_mode='HTML')
            except: 
                try: bot.edit_message_reply_markup(chat_id=admin_msg.chat.id, message_id=admin_msg.message_id, reply_markup=None)
                except: pass
                
            try: 
                red_mesaji = "❌ Maalesef gönderdiğiniz resim reddedildi."
                if orig_msg_id: bot.send_message(user_id, red_mesaji, reply_to_message_id=orig_msg_id)
                else: bot.send_message(user_id, red_mesaji)
            except: pass
            bot.answer_callback_query(call.id, "İçerik reddedildi.")
        except: pass

    elif action == "banuser":
        if str(user_id) == str(PATRON_ID):
            bot.answer_callback_query(call.id, "Patron banlanamaz!", show_alert=True)
            return
            
        bans_col.update_one({"user_id": int(user_id)}, {"$set": {"user_id": int(user_id)}}, upsert=True)
        
        yeni_baslik = f"🚫 {admin_etiket} Tarafından BANLANDI (Kullanıcı Yasaklandı)\n\n{html_full_caption}"
        if len(yeni_baslik) > 1024:
            yeni_baslik = yeni_baslik[:1020] + "..."
            
        try: bot.edit_message_caption(yeni_baslik, chat_id=admin_msg.chat.id, message_id=admin_msg.message_id, reply_markup=None, parse_mode='HTML')
        except: 
            try: bot.edit_message_reply_markup(chat_id=admin_msg.chat.id, message_id=admin_msg.message_id, reply_markup=None)
            except: pass
            
        bot.answer_callback_query(call.id, "Kullanıcı banlandı ve uzaklaştırıldı.", show_alert=True)

# --- RENDER & UPTIMEROBOT İÇİN WEB SUNUCUSU ---
app = Flask(__name__)
@app.route('/')
def home(): return "Oylama Botu Patron & Yayın Moduyla Aktif!"

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    Thread(target=run, daemon=True).start()
    
    # Arka planda hasat işlemi
    Thread(target=gecmisi_hasat_et, daemon=True).start()
    
    # Arka planda zamanlayıcı (Her gün 18:00 mesajı + Pazar 23:59 Birincisi)
    Thread(target=otomatik_mesaj_dongusu, daemon=True).start()
    
    print("Bot Tam Kapasiteyle Başlatıldı!")
    bot.infinity_polling()

