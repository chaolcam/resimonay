import telebot
import os
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

bot = telebot.TeleBot(TOKEN)

# --- MONGODB BAĞLANTISI ---
db_client = pymongo.MongoClient(MONGO_URI)
db = db_client["oylama_botu_veritabani"]
votes_col = db["oylar_kanal"] 

processed_albums = set()

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
    welcome_text = (
        "Hoş geldiniz! Bu bot sayesinde gönderdiğiniz resimleri/videoları oylatabilirsiniz. "
        "Lütfen sadece tek bir resim veya video gönderiniz. "
        "Gönderiniz admin onayından geçince kanalımızda paylaşılacaktır.\n\n"
        "⚠️ <b>Yasal Uyarı:</b> Burada paylaşılan medyalardaki kişilerin rızası ile atıldığı kabul edilir. "
        "Doğabilecek olası yasal sorunlardan veya sorumluluklardan bot yönetimi sorumlu değildir.\n\n"
        "🏆 Kanalın en iyilerini görmek için /siralama yazabilirsiniz!"
    )
    try:
        bot.reply_to(message, welcome_text, parse_mode="HTML")
    except Exception as e:
        pass

# 2. /siralama Komutu
@bot.message_handler(commands=['siralama'])
def send_ranking(message):
    try:
        all_votes = votes_col.find({})
        ranking_data = []

        for doc in all_votes:
            msg_id = doc.get("msg_id")
            voters = doc.get("voters", {})
            
            if not voters:
                continue
            
            total_votes = len(voters)
            avg_score = sum(voters.values()) / total_votes
            
            ranking_data.append({
                "msg_id": msg_id,
                "avg_score": avg_score,
                "total_votes": total_votes
            })
        
        if not ranking_data:
            bot.reply_to(message, "Henüz hiç oy alan gönderi bulunmuyor.")
            return

        ranking_data.sort(key=lambda x: (x["avg_score"], x["total_votes"]), reverse=True)
        top_10 = ranking_data[:10]
        
        text = "🏆 <b>En Yüksek Puanlı Gönderiler (Top 10)</b> 🏆\n\n"
        
        for i, data in enumerate(top_10, 1):
            msg_id = data["msg_id"]
            avg = data["avg_score"]
            votes_count = data["total_votes"]
            link = f"https://t.me/{CHANNEL_USERNAME}/{msg_id}"
            
            text += f"<b>{i}.</b> <a href='{link}'>Gönderiye Git</a> - ⭐ {avg:.2f} <i>({votes_count} oy)</i>\n"
            
        bot.reply_to(message, text, parse_mode="HTML", disable_web_page_preview=True)
        
    except Exception as e:
        bot.reply_to(message, "Sıralama oluşturulurken bir hata oluştu.")
        print(f"Sıralama hatası: {e}")

# --- YENİ EKLENEN: /sil Komutu (Veritabanı Temizliği) ---
@bot.message_handler(commands=['sil'])
def delete_db_record(message):
    # İsteğe bağlı güvenlik: Komutun sadece Admin grubunda çalışmasını istersen alttaki '#' işaretlerini kaldır
    # if message.chat.id != ADMIN_GROUP_ID:
    #     return

    parts = message.text.split()
    
    if len(parts) != 2:
        bot.reply_to(message, "⚠️ Eksik komut girdiniz.\n\n<b>Kullanım:</b> /sil mesaj_id\n<b>Örnek:</b> /sil 1234", parse_mode="HTML")
        return
        
    try:
        msg_id = int(parts[1])
        sonuc = votes_col.delete_one({"msg_id": msg_id})
        
        if sonuc.deleted_count > 0:
            bot.reply_to(message, f"✅ <b>Başarılı!</b> {msg_id} ID'li gönderi ve ona ait tüm oylar veritabanından silindi. Artık sıralamada görünmeyecek.", parse_mode="HTML")
        else:
            bot.reply_to(message, f"❌ Veritabanında <b>{msg_id}</b> ID'sine ait bir oylama kaydı bulunamadı.", parse_mode="HTML")
            
    except ValueError:
        bot.reply_to(message, "⚠️ Mesaj ID'si sadece rakamlardan oluşmalıdır (Örn: /sil 1234).")
    except Exception as e:
        bot.reply_to(message, f"❌ Bir hata oluştu: {e}")

# 3. Özel mesajdan gelenleri yakala
@bot.message_handler(content_types=['photo', 'video'], chat_types=['private'])
def handle_media(message):
    user_id = message.chat.id
    caption = message.caption if message.caption else ""
    user_name = message.from_user.first_name
    orig_msg_id = message.message_id  

    if message.media_group_id:
        if message.media_group_id not in processed_albums:
            processed_albums.add(message.media_group_id)
            try:
                bot.reply_to(message, "⚠️ Lütfen medyaları albüm olarak değil, tek tek/sadece tek bir resim veya video olarak gönderiniz.")
            except:
                pass
        return

    if message.from_user.username:
        user_link = f"@{message.from_user.username}"
    else:
        user_link = f'<a href="tg://user?id={user_id}">{user_name}</a>'

    markup = InlineKeyboardMarkup()
    btn_approve = InlineKeyboardButton("Onayla ✅", callback_data=f"approve_{user_id}_{orig_msg_id}")
    btn_reject = InlineKeyboardButton("Reddet ❌", callback_data=f"reject_{user_id}_{orig_msg_id}")
    markup.add(btn_approve, btn_reject)

    admin_text = f"<b>Gönderen:</b> {user_link}\n\n{caption}"
    
    try:
        if message.content_type == 'photo':
            file_id = message.photo[-1].file_id
            bot.send_photo(ADMIN_GROUP_ID, file_id, caption=admin_text, reply_markup=markup, parse_mode='HTML')
        elif message.content_type == 'video':
            file_id = message.video.file_id
            bot.send_video(ADMIN_GROUP_ID, file_id, caption=admin_text, reply_markup=markup, parse_mode='HTML')
            
        bot.reply_to(message, "Resminiz adminlerimize iletilmiştir, lütfen bekleyiniz.")
    except Exception as e:
        print(f"Hata: {e}")

# 4. TARTIŞMA GRUBU YAKALAYICISI
@bot.message_handler(content_types=['photo', 'video', 'text'], func=lambda m: getattr(m, 'is_automatic_forward', False))
def handle_group_forwards(message):
    if message.forward_from_chat and message.forward_from_chat.id == TARGET_CHANNEL_ID:
        channel_msg_id = message.forward_from_message_id
        group_msg_id = message.message_id
        group_chat_id = message.chat.id
        
        markup = generate_rating_keyboard(channel_msg_id)
        
        try:
            reply_text = "👇 Oylamaya bu tartışma grubundan da katılabilirsiniz 👇"
            reply_msg = bot.send_message(
                chat_id=group_chat_id, 
                text=reply_text, 
                reply_to_message_id=group_msg_id, 
                reply_markup=markup
            )
            
            votes_col.update_one(
                {"msg_id": channel_msg_id}, 
                {"$set": {"group_reply_msg_id": reply_msg.message_id, "group_chat_id": group_chat_id}}, 
                upsert=True
            )
        except Exception as e:
            print(f"Tartışma grubuna buton eklerken hata: {e}")

# 5. Buton tıklamalarını işleme
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    data = call.data
    
    # --- OYLAMA KISMI ---
    if data.startswith("r_"):
        try:
            _, msg_id_str, score_str = data.split("_")
            msg_id = int(msg_id_str)
            score = int(score_str)
            voter_id = str(call.from_user.id)
            
            doc = votes_col.find_one({"msg_id": msg_id})
            if not doc:
                votes_col.insert_one({"msg_id": msg_id, "voters": {}})
                msg_votes = {}
            else:
                msg_votes = doc.get("voters", {})
                
            current_vote = msg_votes.get(voter_id)
            
            if current_vote == score:
                bot.answer_callback_query(call.id, f"Zaten bu resme {score} puan vermişsiniz!")
                return
            
            msg_votes[voter_id] = score
            votes_col.update_one({"msg_id": msg_id}, {"$set": {"voters": msg_votes}}, upsert=True)
            
            total_votes = len(msg_votes)
            avg_score = sum(msg_votes.values()) / total_votes
            
            # --- 1. KANALI GÜNCELLEME ---
            try:
                full_caption = call.message.caption if call.message.caption else ""
                if "📊 Oylama Sonucu:" in full_caption:
                    base_caption = full_caption.split("📊 Oylama Sonucu:")[0].strip()
                else:
                    base_caption = full_caption.strip()
                
                if base_caption:
                    new_caption = f"{base_caption}\n\n📊 Oylama Sonucu:\n⭐ Ortalama: {avg_score:.2f} / 10 ({total_votes} oy)"
                else:
                    new_caption = f"📊 Oylama Sonucu:\n⭐ Ortalama: {avg_score:.2f} / 10 ({total_votes} oy)"
                    
                new_markup = generate_rating_keyboard(msg_id)
                
                bot.edit_message_caption(
                    chat_id=TARGET_CHANNEL_ID,
                    message_id=msg_id,
                    caption=new_caption,
                    reply_markup=new_markup
                )
            except Exception as e:
                pass 
                
            new_markup = generate_rating_keyboard(msg_id)
            
            # --- 2. TARTIŞMA GRUBU YANIT MESAJINI GÜNCELLEME ---
            doc = votes_col.find_one({"msg_id": msg_id}) 
            if doc and "group_reply_msg_id" in doc and "group_chat_id" in doc:
                try:
                    group_text = f"👇 Oylamaya bu tartışma grubundan da katılabilirsiniz 👇\n\n📊 Oylama Sonucu:\n⭐ Ortalama: {avg_score:.2f} / 10 ({total_votes} oy)"
                    bot.edit_message_text(
                        chat_id=doc["group_chat_id"],
                        message_id=doc["group_reply_msg_id"],
                        text=group_text,
                        reply_markup=new_markup
                    )
                except Exception as e:
                    pass

            bot.answer_callback_query(call.id, f"Başarılı: {score} puan verdiniz!")
            
        except Exception as e:
            print(f"Puanlama Hatası: {e}")
            bot.answer_callback_query(call.id, "Puanınız işlenirken bir hata oluştu.")
        return

    # --- ONAY / RED KISMI ---
    admin_msg = call.message
    parts = data.split("_")
    action = parts[0]
    user_id = parts[1]
    orig_msg_id = int(parts[2]) if len(parts) > 2 else None 
    
    plain_caption = admin_msg.caption if admin_msg.caption else ""
    if "\n\n" in plain_caption:
        original_caption = plain_caption.split("\n\n", 1)[1]
    else:
        original_caption = ""
    original_caption = original_caption.strip()

    html_full_caption = admin_msg.html_caption if admin_msg.html_caption else plain_caption

    if action == "approve":
        try:
            sent_msg = None
            if original_caption:
                initial_caption = f"{original_caption}\n\n📊 Oylama Sonucu:\n⭐ Henüz oy verilmedi."
            else:
                initial_caption = "📊 Oylama Sonucu:\n⭐ Henüz oy verilmedi."
            
            if admin_msg.content_type == 'photo':
                sent_msg = bot.send_photo(
                    TARGET_CHANNEL_ID, 
                    admin_msg.photo[-1].file_id, 
                    caption=initial_caption
                )
            elif admin_msg.content_type == 'video':
                sent_msg = bot.send_video(
                    TARGET_CHANNEL_ID, 
                    admin_msg.video.file_id, 
                    caption=initial_caption
                )
            
            post_link = ""
            if sent_msg:
                votes_col.insert_one({"msg_id": sent_msg.message_id, "voters": {}})
                
                initial_markup = generate_rating_keyboard(sent_msg.message_id)
                bot.edit_message_reply_markup(chat_id=TARGET_CHANNEL_ID, message_id=sent_msg.message_id, reply_markup=initial_markup)

                post_link = f"https://t.me/{CHANNEL_USERNAME}/{sent_msg.message_id}"

            bot.edit_message_caption(f"✅ ONAYLANDI\n\n{html_full_caption}", chat_id=admin_msg.chat.id, message_id=admin_msg.message_id, reply_markup=None, parse_mode='HTML')
            
            try: 
                bildirim_mesaji = f"🎉 Resminiz onaylandı ve kanalımızda paylaşıldı!\n\nBuradaki linkten ulaşabilirsiniz:\n{post_link}"
                if orig_msg_id:
                    bot.send_message(user_id, bildirim_mesaji, reply_to_message_id=orig_msg_id)
                else:
                    bot.send_message(user_id, bildirim_mesaji)
            except: pass
                
            bot.answer_callback_query(call.id, "İçerik kanalda paylaşıldı!")
            
        except Exception as e:
            bot.answer_callback_query(call.id, "Hata! Botun kanalda admin olup olmadığını kontrol edin.")
            print(f"Paylaşım Hatası: {e}")

    elif action == "reject":
        try:
            bot.edit_message_caption(f"❌ REDDEDİLDİ\n\n{html_full_caption}", chat_id=admin_msg.chat.id, message_id=admin_msg.message_id, reply_markup=None, parse_mode='HTML')
            try: 
                red_mesaji = "❌ Maalesef gönderdiğiniz resim reddedildi."
                if orig_msg_id:
                    bot.send_message(user_id, red_mesaji, reply_to_message_id=orig_msg_id)
                else:
                    bot.send_message(user_id, red_mesaji)
            except: pass
            bot.answer_callback_query(call.id, "İçerik reddedildi.")
        except Exception as e:
            pass

# --- RENDER & UPTIMEROBOT İÇİN WEB SUNUCUSU ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Oylama Botu (Tartışma Grubu Desteğiyle) Aktif!"

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

if __name__ == "__main__":
    keep_alive() 
    print("Bot tartışma grubu oylama eşitlemesiyle başlatıldı!")
    bot.infinity_polling()
