import time
from types import SimpleNamespace as NS
import pytest
from app.broker import MT5Broker, BrokerUnavailable
from app.engine import Plan, calculate


class FakeTerminal:
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1

    def __init__(self):
        self.account = NS(login=42,server='Test',currency='USD',equity=1000,balance=1000)
        self.tick = NS(time=time.time(),ask=1.1,bid=1.0999)
        self.spec = NS(name='EURUSDm',trade_calc_mode=0,trade_tick_size=.00001,digits=5,trade_contract_size=100000,volume_min=.01,volume_step=.01,volume_max=100)
        self.connected = True

    def initialize(self,path,timeout): return True
    def terminal_info(self): return NS(connected=self.connected)
    def account_info(self): return self.account
    def symbol_info(self,symbol): return self.spec if symbol == self.spec.name else None
    def symbols_get(self): return [self.spec,NS(name='CFD',trade_calc_mode=1)]
    def symbol_select(self,symbol,selected): return True
    def symbol_info_tick(self,symbol): return self.tick
    def order_calc_profit(self,action,symbol,volume,entry,stop):
        return (stop-entry)*100000*volume*(1 if action==0 else -1)


@pytest.fixture
def broker(monkeypatch):
    monkeypatch.setenv('MT5_PATH','test-terminal.exe')
    monkeypatch.setenv('MT5_LOGIN','42')
    monkeypatch.setenv('MT5_SERVER','Test')
    monkeypatch.setenv('MAX_TICK_AGE_SECONDS','120')
    b=MT5Broker(); b.mt5=FakeTerminal(); return b


def test_live_adapter_contract(broker):
    assert broker.symbols()==['EURUSDm']
    result=calculate(Plan(symbol='EURUSDm',entry=1.1,stop=1.09,count=4,budget=60),broker)
    assert result['total_loss']==50
    assert result['context']['mode']=='mt5'


def test_cent_currency_preserved(broker):
    broker.mt5.account.currency='USC'
    context=broker.context('EURUSDm')
    assert context['account']['currency']=='USC'
    assert 'USC' in context['warnings'][0]


@pytest.mark.parametrize('mutation',[
    lambda m:setattr(m.account,'login',43),
    lambda m:setattr(m.account,'server','Wrong'),
    lambda m:setattr(m,'connected',False),
    lambda m:setattr(m.tick,'time',time.time()-1000),
    lambda m:setattr(m.tick,'ask',0),
])
def test_bad_connection_data_rejected(broker,mutation):
    mutation(broker.mt5)
    with pytest.raises(BrokerUnavailable): broker.context('EURUSDm')


def test_profit_failure_not_zero(broker):
    broker.mt5.order_calc_profit=lambda *args:None
    with pytest.raises(BrokerUnavailable): broker.profit('EURUSDm','buy',.01,1.1,1.09)


def test_non_forex_rejected(broker):
    broker.mt5.spec.trade_calc_mode=1
    with pytest.raises(ValueError): broker.context('EURUSDm')
