import os
import logging
from datetime import datetime
from typing import Dict, Tuple, Any
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command
import asyncio 
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import shlex

logging.basicConfig(level=logging.INFO)

from dotenv import load_dotenv
load_dotenv()
bot = Bot(token=os.getenv('BOT_TOKEN'))

storage = MemoryStorage()
dp = Dispatcher(bot=bot, storage=storage)
scheduler = AsyncIOScheduler()

disputes: Dict[Tuple[int, str], Dict[str, Any]] = {}



@dp.message(Command('how_to_use'))
async def how_to_use(message: types.Message):
    await message.reply(
        "Для создания спора используй:\n"
        "`/create_dispute  'Название спора' 'Описание, критерии' 'Дата и время окончания приема ставок' 'Дата и время окончания спора'`\n\n"
        f"• Формат даты: `'{datetime.now().strftime('%Y-%m-%d %H:%M')}'`\n"
        "• Пример:\n"
        "`/create_dispute 'Погода 25 мая' 'Будет дождь в Москве' '2024-05-24 20:00' '2024-05-25 20:00'`\n\n"
        "Для ставки:\n"
        "`/bet Название_спора [T/F] Сумма`\n"
        "• Пример: `/bet 'Погода 25 мая' T 1000`\n\n"
        "Для голосования:\n"
        "`/vote Название_спора [T/F]`\n"
        "• Пример: `/vote 'Погода 25 мая' F`",
        parse_mode="MarkdownV2"
    )

@dp.message(Command("list_disputes"))
async def list_disputes(message: types.Message):
    chat_id = message.chat.id
    current_disputes = [
        dispute for (cid, name), dispute in disputes.items() 
        if cid == chat_id
    ]

    if not current_disputes:
        await message.reply("🚫 В этом чате нет активных споров")
        return

    response = ["📋 Список активных споров:\n"]
    
    for dispute in current_disputes:
        response.append(
            f"▫️ *{dispute['name']}*\n"
            f"├ Описание: {dispute['description']}\n"
            f"├ Ставки до: {dispute['end_bet_time'].strftime('%Y-%m-%d %H:%M')}\n"
            f"└ Завершение: {dispute['end_dispute_time'].strftime('%Y-%m-%d %H:%M')}\n"
        )

    # Разбиваем сообщение на части, если слишком длинное
    full_message = "\n".join(response)
    for part in [full_message[i:i+4096] for i in range(0, len(full_message), 4096)]:
        await message.answer(part, parse_mode="Markdown")





async def resolve_dispute(chat_id: int, name: str):
    key = (chat_id, name)
    if key not in disputes:
        return

    dispute = disputes[key]
    """
    if datetime.now() < dispute['end_dispute_time']:
        return
    """

    if not dispute['votes']:
        await bot.send_message(chat_id, f"Спор '{name}' не разрешён: нет голосов.")
        # TODO что то делать в этой ситуации
        return

    t_votes = sum(1 for v in dispute['votes'].values() if v)
    f_votes = len(dispute['votes']) - t_votes

    t_bets_sum = sum(b["sum"] for b in dispute['bets'] if b["on"]=='T')
    f_bets_sum = sum(b["sum"] for b in dispute['bets'] if b["on"]=='F')

    msg=f"Спор {dispute["name"]} завершен!\nРезультаты:\n"

    # TODO обработка нулевых ставок
    if t_bets_sum == 0 or f_bets_sum == 0:
        await bot.send_message(chat_id, "можно пока без нулевых ставок пж")
        return

    for i in range(len(dispute['bets'])):
        b = dispute['bets'][i]
        dispute['bets'][i].result = (t_votes/(t_votes + f_votes) if b["on"]=='T' else f_votes/(t_votes + f_votes)) *\
        (b["sum"] + (b["sum"]/(t_bets_sum if b["on"]=='T' else f_bets_sum)) *\
        (f_bets_sum if b["on"]=='T' else t_bets_sum))
        # TODO добавить вывод ника вместо id
        msg += f"👤 {b["uid"]} получает {dispute['bets'][i]["result"]} \n"
        # TODO добавить переводы тон коинов для реальных ставок

    await bot.send_message(chat_id, msg)
    del disputes[key]


@dp.message(Command('create_dispute'))
async def create_disput(message: types.Message):
    try:
        # Разбиваем аргументы с учетом кавычек
        args = shlex.split(message.text)[1:] 
    except ValueError as e:
        await message.reply(f"Ошибка в формате аргументов: {e}")
        return
    
    if len(args) < 4:
        await message.reply("Используйте: /create_disput [имя] [описание] [конец_ставок] [конец_спора]")
        return

    name, desc = args[0], ' '.join(args[1:-2])
    try:
        end_bet = datetime.strptime(args[-2], '%Y-%m-%d %H:%M')
        end_disp = datetime.strptime(args[-1], '%Y-%m-%d %H:%M')
    except ValueError:
        await message.reply("Формат даты: ГГГГ-ММ-ДД ЧЧ:ММ")
        return

    if end_bet >= end_disp:
        await message.reply("Конец ставок должен быть раньше конца спора.")
        return
    
    if end_bet <= datetime.now() or end_disp <= datetime.now():
        await message.reply("Конец ставок должен быть позже текущего момента.")
        return

    chat_id = message.chat.id
    key = (chat_id, name)
    if key in disputes:
        await message.reply("Спор уже существует.")
        return

    disputes[key] = {
        'name': name,
        'description': desc,
        'end_bet_time': end_bet,
        'end_dispute_time': end_disp,
        'bets': [], # {on: (T/F), uid, sum, result}
        'votes': {},
        'chat_id': chat_id,
    }

    # TODO добавить предупреждение об окончании приема ставок и о конце спора за n минут
    scheduler.add_job(
        resolve_dispute, 
        'date',
        run_date=end_disp,
        args=[chat_id, name],
        misfire_grace_time=60,  # Разрешить опоздание до 60 сек
        coalesce=True,          # Объединить пропущенные задачи в одну
        id=f"resolve_{chat_id}_{name}")
    
    await message.reply(f"Спор '{name}' создан.\nСтавки до {end_bet.strftime('%Y-%m-%d %H:%M')}.\nОкончание спора: {end_disp.strftime('%Y-%m-%d %H:%M')}\n\nОписание:\n{desc}")

@dp.message(Command('bet'))
async def bet(message: types.Message):
    try:
        # Разбиваем аргументы с учетом кавычек
        args = shlex.split(message.text)[1:] 
    except ValueError as e:
        await message.reply(f"Ошибка в формате аргументов: {e}")
        return
    
    if len(args) != 3:
        await message.reply("Используйте: /bet [имя] [T/F] [сумма]")
        return

    name, var, sum_str = args
    if var not in ('T', 'F'):
        await message.reply("Вариант: T или F.")
        return

    try:
        bet_sum = float(sum_str)
        if bet_sum <= 0:
            raise ValueError
    except ValueError:
        await message.reply("Сумма должна быть числом > 0.")
        return

    key = (message.chat.id, name)
    if key not in disputes:
        await message.reply("Спор не найден.")
        return

    dispute = disputes[key]
    if datetime.now() > dispute['end_bet_time']:
        await message.reply("Время ставок истекло.")
        return


    uid = message.from_user.id
    existing_bet = next((b for b in dispute['bets'] if b['uid'] == uid and b['on'] == var), None)
    
    if existing_bet:
        existing_bet['sum'] += bet_sum
        existing_bet['result'] += bet_sum
    else:
        dispute['bets'].append({
            'uid': uid,
            'on': var,
            'sum': bet_sum,
            'result': bet_sum
        })

    # TODO добавить переводы тон коинов для реальных ставок
    
    msg = "Ставка принята.\nТекущие ставки:\n"
    for b in dispute['bets']:
        # TODO добавить вывод ника вместо id
        msg += f"👤 {b["uid"]} ставит {b["sum"]} на {"успех" if b["on"] == 'T' else "неудачу"}\n"

    await message.reply(msg)


@dp.message(Command('vote'))
async def vote(message: types.Message):
    try:
        # Разбиваем аргументы с учетом кавычек
        args = shlex.split(message.text)[1:] 
    except ValueError as e:
        await message.reply(f"Ошибка в формате аргументов: {e}")
        return
    
    if len(args) != 2:
        await message.reply("Используйте: /vote [имя] [T/F]")
        return

    name, var = args
    if var not in ('T', 'F'):
        await message.reply("Вариант: T или F.")
        return

    key = (message.chat.id, name)
    if key not in disputes:
        await message.reply("Спор не найден.")
        return

    dispute = disputes[key]
    now = datetime.now()
    if now < dispute['end_bet_time'] or now > dispute['end_dispute_time']:
        await message.reply("Голосование невозможно.")
        return

    uid = message.from_user.id

    user_has_bet = any(b['uid'] == uid for b in dispute['bets'])
    if user_has_bet:
        await message.reply("Ставившие не голосуют.")
        return

    dispute['votes'][uid] = (var == 'T')
    await message.reply("Голос учтён.")



async def main():
    scheduler.start()
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())