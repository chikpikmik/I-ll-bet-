import asyncio
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Настройка логгирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота и диспетчера
bot = Bot(token="YOUR_BOT_TOKEN")
dp = Dispatcher()

# Хранение данных (в реальном проекте используйте БД)
active_disputes = {}
bets = {}
judges_votes = {}
dispute_votes = {}

class Form(StatesGroup):
    name = State()
    options = State()
    end_date = State()
    judges = State()

# Стартовая команда
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Я бот для организации споров. Чтобы создать спор используй /create_dispute"
    )

# Создание спора
@dp.message(Command("create_dispute"))
async def cmd_create_dispute(message: types.Message, state: FSMContext):
    await state.set_state(Form.name)
    await message.answer("Введите название спора:")

@dp.message(Form.name)
async def process_name(message: types.Message, state: FSMContext):
    if message.text in active_disputes:
        await message.answer("Спор с таким названием уже существует!")
        return
        
    await state.update_data(name=message.text)
    await state.set_state(Form.options)
    await message.answer("Введите варианты через запятую (минимум 2):")

@dp.message(Form.options)
async def process_options(message: types.Message, state: FSMContext):
    options = [x.strip() for x in message.text.split(",")]
    if len(options) < 2:
        await message.answer("Нужно минимум 2 варианта!")
        return
        
    await state.update_data(options=options)
    await state.set_state(Form.end_date)
    await message.answer("Введите дату окончания (ДД.ММ.ГГГГ):")

@dp.message(Form.end_date)
async def process_end_date(message: types.Message, state: FSMContext):
    try:
        end_date = datetime.strptime(message.text, "%d.%m.%Y")
    except ValueError:
        await message.answer("Неверный формат даты!")
        return
        
    await state.update_data(end_date=end_date)
    await state.set_state(Form.judges)
    await message.answer("Введите юзернеймы судей через запятую (@user1, @user2):")

@dp.message(Form.judges)
async def process_judges(message: types.Message, state: FSMContext):
    judges = [x.strip().lower() for x in message.text.split(",")]
    data = await state.get_data()
    
    active_disputes[data['name']] = {
        'options': data['options'],
        'end_date': data['end_date'],
        'judges': judges,
        'creator': message.from_user.username,
        'bank': 0,
        'participants': []
    }
    
    bets[data['name']] = {}
    await state.clear()
    await message.answer(f"Спор '{data['name']}' создан!")

# Ставка
@dp.message(Command("bet"))
async def cmd_bet(message: types.Message):
    args = message.text.split()[1:]
    if len(args) != 3:
        await message.answer("Использование: /bet [спор] [вариант] [сумма]")
        return
        
    dispute_name, option, amount = args
    user = message.from_user.username
    
    if dispute_name not in active_disputes:
        await message.answer("Спор не найден!")
        return
        
    dispute = active_disputes[dispute_name]
    
    if option not in dispute['options']:
        await message.answer("Неверный вариант!")
        return
        
    try:
        amount = int(amount)
    except ValueError:
        await message.answer("Неверная сумма!")
        return
        
    if user not in bets[dispute_name]:
        bets[dispute_name][user] = {'option': option, 'amount': amount}
        dispute['bank'] += amount
        dispute['participants'].append(user)
        await message.answer(f"Ставка принята! Текущий банк: {dispute['bank']}")
    else:
        await message.answer("Вы уже сделали ставку в этом споре!")

# Разрешение спора
@dp.message(Command("resolve_dispute"))
async def cmd_resolve(message: types.Message):
    dispute_name = message.text.split()[1]
    
    if dispute_name not in active_disputes:
        await message.answer("Спор не найден!")
        return
        
    dispute = active_disputes[dispute_name]
    
    if datetime.now() < dispute['end_date']:
        await message.answer("Дата окончания спора еще не наступила!")
        return
        
    judges = dispute['judges']
    builder = InlineKeyboardBuilder()
    
    for option in dispute['options']:
        builder.button(text=option, callback_data=f"vote_{dispute_name}_{option}")
    
    for judge in judges:
        await bot.send_message(
            chat_id=message.chat.id,
            text=f"{judge}, проголосуйте за результат спора:",
            reply_markup=builder.as_markup()
        )
    
    dispute_votes[dispute_name] = {'votes': {}, 'total_judges': len(judges)}

# Обработка голосов
@dp.callback_query(F.data.startswith("vote_"))
async def process_vote(callback: types.CallbackQuery):
    _, dispute_name, option = callback.data.split("_", 2)
    judge = callback.from_user.username.lower()
    
    if judge not in active_disputes[dispute_name]['judges']:
        await callback.answer("Вы не судья в этом споре!")
        return
        
    if judge in dispute_votes[dispute_name]['votes']:
        await callback.answer("Вы уже проголосовали!")
        return
        
    dispute_votes[dispute_name]['votes'][judge] = option
    await callback.answer(f"Ваш голос за {option} учтен!")
    
    # Проверка завершения голосования
    if len(dispute_votes[dispute_name]['votes']) == dispute_votes[dispute_name]['total_judges']:
        await finish_voting(dispute_name, callback.message.chat.id)

async def finish_voting(dispute_name: str, chat_id: int):
    votes = dispute_votes[dispute_name]['votes']
    result = max(set(votes.values()), key=list(votes.values()).count)
    
    dispute = active_disputes[dispute_name]
    total_bank = dispute['bank']
    participants = bets[dispute_name]
    
    winning_bets = [user for user, bet in participants.items() if bet['option'] == result]
    total_winning = sum(bet['amount'] for bet in participants.values() if bet['option'] == result)
    
    if total_winning == 0:
        await bot.send_message(chat_id, "Нет выигравших ставок!")
        return
        
    # Распределение банка
    payouts = {}
    for user in winning_bets:
        share = participants[user]['amount'] / total_winning
        payouts[user] = round(total_bank * share, 2)
    
    # Формирование результата
    result_text = f"Результат спора '{dispute_name}': {result}\nВыплаты:\n"
    for user, amount in payouts.items():
        result_text += f"@{user}: {amount}\n"
    
    await bot.send_message(chat_id, result_text)
    
    # Очистка данных
    del active_disputes[dispute_name]
    del bets[dispute_name]
    del dispute_votes[dispute_name]

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())