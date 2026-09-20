from decimal import Decimal
import pytest
from app.broker import DemoBroker, MT5Broker, BrokerUnavailable
from app.stops import SwingRequest, StepRequest, find_swing, step_stop


def swing(**kwargs):
    return SwingRequest(**({'symbol':'EURUSD','entry':1.1,'side':'buy'} | kwargs))


class BarsBroker(DemoBroker):
    lows = [1.095,1.094,1.090,1.094,1.095,1.093,1.091,1.093,1.094,1.088]
    def bars(self,*args):
        return [{'time':100+i,'low':v,'high':2-v} for i,v in enumerate(self.lows)]


def test_latest_confirmed_not_latest_extreme():
    result = find_swing(swing(),BarsBroker())
    assert result['swing_time'] == 106
    assert result['stop'] == 1.091  # final unconfirmed low 1.088 is excluded


def test_sell_high():
    result = find_swing(swing(side='sell',entry=.90),BarsBroker())
    assert result['kind']=='high'
    assert result['swing_time']==106


def test_wrong_side_falls_back_to_older_swing():
    assert find_swing(swing(entry=1.0905),BarsBroker())['stop']==1.09


def test_buffer_and_no_candidate():
    assert find_swing(swing(buffer_ticks=10),BarsBroker())['stop']==1.0909
    with pytest.raises(ValueError,match='Không tìm thấy'):
        find_swing(swing(entry=1.08),BarsBroker())


def test_equal_lows_not_strict_pivot():
    b=BarsBroker(); b.lows=[1.1,1.09,1.09,1.1,1.1]
    with pytest.raises(ValueError): find_swing(swing(),b)


@pytest.mark.parametrize('symbol,entry,stop,tick',[('EURUSD',1.1,1.09,.00001),('USDJPY',150,149,.001)])
def test_ticker_specific_step_and_roundtrip(symbol,entry,stop,tick):
    b=DemoBroker()
    for ticks in (1,10,100):
        req=StepRequest(symbol=symbol,side='buy',entry=entry,stop=stop,direction='up',ticks=ticks)
        up=step_stop(req,b)['stop']
        assert Decimal(str(up))==Decimal(str(stop))+Decimal(str(tick))*ticks
        down=step_stop(req.model_copy(update={'stop':up,'direction':'down'}),b)['stop']
        assert down==stop


def test_tick_not_same_as_decimal_precision():
    class Quarter(DemoBroker):
        def context(self,symbol):
            c=super().context(symbol); c['symbol']['tick_size']=.25; c['symbol']['digits']=2; return c
    req=StepRequest(symbol='EURUSD',side='buy',entry=100,stop=90.12,direction='up')
    assert step_stop(req,Quarter())['stop']==90.25
    assert step_stop(req.model_copy(update={'direction':'down'}),Quarter())['stop']==90


def test_cannot_cross_entry():
    with pytest.raises(ValueError):
        step_stop(StepRequest(symbol='EURUSD',entry=1.1,stop=1.09999,side='buy',direction='up'),DemoBroker())


def test_mt5_skips_forming_bar():
    class Terminal:
        TIMEFRAME_M15=15
        def copy_rates_from_pos(self,symbol,tf,start,count):
            assert (symbol,tf,start,count)==('EURUSD',15,1,300)
            return [{'time':100,'high':1.1,'low':1.09}]
    b=MT5Broker(); b.mt5=Terminal()
    assert b.bars('EURUSD','M15',300)[0]['time']==100


def test_missing_history_error():
    class Terminal:
        TIMEFRAME_M15=15
        def copy_rates_from_pos(self,*args): return None
    b=MT5Broker(); b.mt5=Terminal()
    with pytest.raises(BrokerUnavailable): b.bars('EURUSD','M15',300)


def test_demo_swing_frames_and_sides():
    for symbol,entry in [('EURUSD',1.1),('USDJPY',150)]:
        for timeframe in ('M1','M5','M15','M30','H1','H4','D1'):
            for side in ('buy','sell'):
                result=find_swing(swing(symbol=symbol,entry=entry,timeframe=timeframe,side=side),DemoBroker())
                assert (result['stop'] < entry) if side=='buy' else (result['stop']>entry)
