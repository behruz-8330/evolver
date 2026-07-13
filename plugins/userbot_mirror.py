from aiogram import Router, F
from aiogram.types import Message, ContentType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# Bot uchun router obyektini yaratamiz
router = Router()

# "Mirror" (qayta yuborish) funksiyasi uchun holatlar (states) guruhini aniqlaymiz
class MirrorStates(StatesGroup):
    """
    Foydalanuvchining navbatdagi xabarini kutish holatini ifodalaydi.
    """
    waiting_for_message = State()

@router.message(Command("mirror", description="Menga jo'natgan xabaringizni o'zimni nomimdan qayta yuboraman."))
async def cmd_mirror(message: Message, state: FSMContext):
    """
    /mirror buyrug'ini qabul qiladi.
    Foydalanuvchidan qayta yuborilishi kerak bo'lgan xabarni yuborishni so'raydi.
    """
    await message.reply(
        "Mengaga qanday xabar (matn, rasm, video, hujjat va h.k.) yuborsangiz, "
        "o'zimni nomimdan sizga qayta yuboraman. Bu xabarning asl yuboruvchisi "
        "haqidagi ma'lumotlarni (masalan, 'forwarded from' tegini) olib tashlaydi. "
        "Marhamat, yuboring."
    )
    # Botni "waiting_for_message" holatiga o'tkazamiz
    await state.set_state(MirrorStates.waiting_for_message)

@router.message(
    MirrorStates.waiting_for_message,
    F.content_type.in_([
        ContentType.TEXT, ContentType.PHOTO, ContentType.VIDEO, ContentType.DOCUMENT,
        ContentType.AUDIO, ContentType.VOICE, ContentType.STICKER, ContentType.ANIMATION,
        ContentType.VIDEO_NOTE
    ])
)
async def process_mirror_message(message: Message, state: FSMContext):
    """
    `MirrorStates.waiting_for_message` holatida kelgan turli xil xabarlarni qayta ishlaydi.
    Xabarni bot nomidan foydalanuvchiga qayta yuboradi (forward tegisiz).
    """
    try:
        # Xabar turiga qarab mos funksiya orqali qayta yuboramiz
        if message.text:
            await message.answer(message.text, entities=message.entities)
        elif message.photo:
            await message.answer_photo(
                photo=message.photo[-1].file_id, 
                caption=message.caption,
                caption_entities=message.caption_entities
            )
        elif message.video:
            await message.answer_video(
                video=message.video.file_id, 
                caption=message.caption,
                caption_entities=message.caption_entities
            )
        elif message.document:
            await message.answer_document(
                document=message.document.file_id, 
                caption=message.caption,
                caption_entities=message.caption_entities
            )
        elif message.audio:
            await message.answer_audio(
                audio=message.audio.file_id, 
                caption=message.caption,
                caption_entities=message.caption_entities
            )
        elif message.voice:
            await message.answer_voice(
                voice=message.voice.file_id, 
                caption=message.caption,
                caption_entities=message.caption_entities
            )
        elif message.sticker:
            await message.answer_sticker(sticker=message.sticker.file_id)
        elif message.animation:
            await message.answer_animation(
                animation=message.animation.file_id, 
                caption=message.caption,
                caption_entities=message.caption_entities
            )
        elif message.video_note:
            await message.answer_video_note(video_note=message.video_note.file_id)
        
        # Xabar qayta yuborilgach, holatni tozalaymiz
        await state.clear()
        
    except Exception as e:
        # Xabar yuborishda xatolik yuz bersa, foydalanuvchiga xabar beramiz
        await message.answer(f"Xabar yuborishda xatolik yuz berdi: {e}. Iltimos, qayta urinib ko'ring.")
        await state.clear()

@router.message(MirrorStates.waiting_for_message)
async def process_unsupported_mirror_message(message: Message, state: FSMContext):
    """
    `MirrorStates.waiting_for_message` holatida kelgan, lekin yuqoridagi handler tomonidan
    qo'llab-quvvatlanmaydigan xabar turlarini (masalan, kontakt, joylashuv, anketa) qayta ishlaydi.
    """
    await message.answer(
        "Kechirasiz, ushbu turdagi xabar (masalan, kontakt, joylashuv, anketa) hozircha qo'llab-quvvatlanmaydi."
    )
    # Holatni tozalaymiz
    await state.clear()