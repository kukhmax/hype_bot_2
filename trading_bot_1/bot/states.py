from aiogram.fsm.state import State, StatesGroup

class SettingsFSM(StatesGroup):
    waiting_for_mode = State()
    waiting_for_risk = State()
    waiting_for_daily_loss = State()
    waiting_for_gemini = State()

class PairFSM(StatesGroup):
    waiting_for_symbol = State()
    waiting_for_timeframe = State()
    waiting_for_leverage = State()

class TestFSM(StatesGroup):
    waiting_for_pair_choice = State()

class TrainMLFSM(StatesGroup):
    waiting_for_pair_choice = State()

class StrategyFSM(StatesGroup):
    waiting_for_param_choice = State()
    waiting_for_value = State()
