from aiogram.fsm.state import State, StatesGroup

class SettingsFSM(StatesGroup):
    waiting_for_mode = State()
    waiting_for_symbol = State()
    waiting_for_timeframe = State()
    waiting_for_risk = State()
