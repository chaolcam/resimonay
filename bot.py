import telebot
import os
from threading import Thread
from flask import Flask
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- AYARLAR ---
TOKEN = os.environ.get("BOT_TOKEN") 
ADMIN_GROUP_ID = -1003791676374
TARGET_GROUP_ID = -1004357691251
TARGET_THREAD_ID = 20969

bot = telebot.TeleBot(TOKEN)

# Hafıza depoları
processed_albums = set()
votes = {}  

# 1'den 10'a kadar olan puanlama butonlarını üreten yardımcı fonksiyon
def generate_rating_keyboard(message_id):
    msg_votes = votes.get(message_id, {})
    
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
        "Gönderiniz admin onayından geçince ilgili konuda paylaşılacaktır."
    )
    try:
        bot.reply_to(message, welcome_text)
    except Exception as e:
        print(f"Kullanıcıya mesaj iletilemedi: {e}")

# 2. Özel mesajdan gelenleri yakala
@bot.message_handler(content_types=['photo', 'video'], chat_types=['private'])
def handle_media(message):
    user_id = message.chat.id
    caption = message.caption if message.caption else ""
    user_name = message.from_user.first_name

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
    btn_approve = InlineKeyboardButton("Onayla ✅", callback_data=f"approve_{user_id}")
    btn_reject = InlineKeyboardButton("Reddet ❌", callback_data=f"reject_{user_id}")
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

# 3. Buton tıklamalarını işleme
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    data = call.data
    
    # --- OYLAMA KISMI ---
    if data.startswith("r_"):
        try:
            _, msg_id_str, score_str = data.split("_")
            msg_id = int(msg_id_str)
            score = int(score_str)
            voter_id = call.from_user.id
            
            if msg_id not in votes:
                votes[msg_id] = {}
                
            current_vote = votes[msg_id].get(voter_id)
            
            if current_vote == score:
                bot.answer_callback_query(call.id, f"Zaten bu resme {score} puan vermişsiniz!")
                return
            
            votes[msg_id][voter_id] = score
            
            msg_votes = votes[msg_id]
            total_votes = len(msg_votes)
            avg_score = sum(msg_votes.values()) / total_votes
            
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
                chat_id=TARGET_GROUP_ID,
                message_id=msg_id,
                caption=new_caption,
                reply_markup=new_markup
            )
            bot.answer_callback_query(call.id, f"Başarılı: {score} puan verdiniz!")
            
        except Exception as e:
            print(f"Puanlama Hatası: {e}")
            bot.answer_callback_query(call.id, "Puanınız işlenirken bir hata oluştu.")
        return

    # --- ONAY / RED KISMI ---
    admin_msg = call.message
    action = data.split("_")[0]
    user_id = data.split("_")[1]
    
    # 1. Hedef gruba göndermek için HTML'siz, dümdüz metni alıyoruz
    plain_caption = admin_msg.caption if admin_msg.caption else ""
    if "\n\n" in plain_caption:
        original_caption = plain_caption.split("\n\n", 1)[1]
    else:
        original_caption = ""
    original_caption = original_caption.strip()

    # 2. Admin grubunu düzenlemek için HTML korumalı metni alıyoruz (Linkler bozulmaz)
    html_full_caption = admin_msg.html_caption if admin_msg.html_caption else plain_caption

    if action == "approve":
        try:
            sent_msg = None
            # Hedef gruba gönderilecek temiz metin
            if original_caption:
                initial_caption = f"{original_caption}\n\n📊 Oylama Sonucu:\n⭐ Henüz oy verilmedi."
            else:
                initial_caption = "📊 Oylama Sonucu:\n⭐ Henüz oy verilmedi."
            
            if admin_msg.content_type == 'photo':
                sent_msg = bot.send_photo(
                    TARGET_GROUP_ID, 
                    admin_msg.photo[-1].file_id, 
                    caption=initial_caption, 
                    message_thread_id=TARGET_THREAD_ID
                )
            elif admin_msg.content_type == 'video':
                sent_msg = bot.send_video(
                    TARGET_GROUP_ID, 
                    admin_msg.video.file_id, 
                    caption=initial_caption, 
                    message_thread_id=TARGET_THREAD_ID
                )
            
            if sent_msg:
                votes[sent_msg.message_id] = {}
                initial_markup = generate_rating_keyboard(sent_msg.message_id)
                bot.edit_message_reply_markup(chat_id=TARGET_GROUP_ID, message_id=sent_msg.message_id, reply_markup=initial_markup)

            # Admin grubundaki mesajı düzenlerken "html_full_caption" kullanıyoruz
            bot.edit_message_caption(f"✅ ONAYLANDI\n\n{html_full_caption}", chat_id=admin_msg.chat.id, message_id=admin_msg.message_id, reply_markup=None, parse_mode='HTML')
            
            try:
                bot.send_message(user_id, "🎉 Resim onaylandı, ilgili konuda resminizi bulabilirsiniz.")
            except:
                pass
                
            bot.answer_callback_query(call.id, "İçerik paylaşıldı!")
            
        except Exception as e:
            bot.answer_callback_query(call.id, "Hata oluştu!")
            print(f"Paylaşım Hatası: {e}")

    elif action == "reject":
        try:
            # Ret durumunda da aynı şekilde HTML korumalı metni kullanıyoruz
            bot.edit_message_caption(f"❌ REDDEDİLDİ\n\n{html_full_caption}", chat_id=admin_msg.chat.id, message_id=admin_msg.message_id, reply_markup=None, parse_mode='HTML')
            try:
                bot.send_message(user_id, "Resminiz reddedildi.")
            except:
                pass
            bot.answer_callback_query(call.id, "İçerik reddedildi.")
        except Exception as e:
            print(f"Reddetme hatası: {e}")

# --- RENDER & UPTIMEROBOT İÇİN WEB SUNUCUSU ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Telegram Botu Aktif ve Çalışıyor!"

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- ÇALIŞTIRMA ---
if __name__ == "__main__":
    keep_alive() 
    print("Bot tüm düzeltmelerle başlatıldı! Bekleniyor...")
    bot.infinity_polling()
