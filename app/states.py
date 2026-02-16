from aiogram.fsm.state import StatesGroup, State

class SignalSetup(StatesGroup):
    waiting_pair = State()
    waiting_tf = State()
    waiting_adx = State()
    waiting_atr = State()
    waiting_risk = State()
