"""Confirmed 2–2 pivots and broker tick-aligned stop adjustments."""
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

Timeframe = Literal['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1']
FRAME_SECONDS = {'M1':60,'M5':300,'M15':900,'M30':1800,'H1':3600,'H4':14400,'D1':86400}


def D(value):
    return Decimal(str(value))


class SwingRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra='forbid')
    symbol: str = Field(min_length=1,max_length=80)
    side: Literal['buy','sell']
    entry: float = Field(gt=0,le=1e9)
    timeframe: Timeframe = 'M15'
    buffer_ticks: int = Field(default=0,ge=0,le=10000)


class StepRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra='forbid')
    symbol: str = Field(min_length=1,max_length=80)
    side: Literal['buy','sell']
    entry: float = Field(gt=0,le=1e9)
    stop: float = Field(gt=0,le=1e9)
    direction: Literal['up','down']
    ticks: Literal[1,10,100] = 1


def valid_stop(side, entry, stop):
    return stop > 0 and (stop < entry if side == 'buy' else stop > entry)


def find_swing(request, broker):
    context = broker.context(request.symbol)
    tick = D(context['symbol']['tick_size'])
    if tick <= 0:
        raise ValueError('Broker chưa cung cấp bước giá hợp lệ.')
    bars = broker.bars(request.symbol, request.timeframe, 300)
    if len(bars) < 5:
        raise ValueError('Chưa đủ 5 nến đã đóng để xác nhận swing. Chọn timeframe khác hoặc nhập SL tay.')
    if any(bars[i]['time'] >= bars[i+1]['time'] for i in range(len(bars)-1)):
        raise ValueError('Thứ tự dữ liệu nến không hợp lệ.')
    field = 'low' if request.side == 'buy' else 'high'
    for i in range(len(bars)-3, 1, -1):
        value = D(bars[i][field])
        neighbours = [D(bars[j][field]) for j in (i-2,i-1,i+1,i+2)]
        pivot = all(value < n for n in neighbours) if field == 'low' else all(value > n for n in neighbours)
        if not pivot or not valid_stop(request.side,D(request.entry),value):
            continue
        offset = tick * request.buffer_ticks
        stop = value-offset if field == 'low' else value+offset
        rounding = ROUND_FLOOR if field == 'low' else ROUND_CEILING
        stop = (stop/tick).to_integral_value(rounding=rounding)*tick
        if not valid_stop(request.side,D(request.entry),stop):
            continue
        broker.verify_account(context['account'])
        return {'stop':float(stop),'swing_price':float(value),'swing_time':int(bars[i]['time']),
                'timeframe':request.timeframe,'kind':field,'buffer_ticks':request.buffer_ticks,
                'bars_scanned':len(bars),'context':context}
    raise ValueError('Không tìm thấy swing 2–2 đã xác nhận ở đúng phía entry trong 300 nến gần nhất. Đổi timeframe hoặc nhập SL tay.')


def step_stop(request, broker):
    context = broker.context(request.symbol)
    tick = D(context['symbol']['tick_size'])
    if tick <= 0:
        raise ValueError('Broker chưa cung cấp bước giá hợp lệ.')
    up = request.direction == 'up'
    index = (D(request.stop)/tick).to_integral_value(rounding=ROUND_FLOOR if up else ROUND_CEILING)
    stop = (index + (request.ticks if up else -request.ticks))*tick
    if not valid_stop(request.side,D(request.entry),stop):
        raise ValueError('Mức SL mới không hợp lệ: Buy cần SL dưới entry, Sell cần SL trên entry.')
    broker.verify_account(context['account'])
    return {'stop':float(stop),'context':context}
