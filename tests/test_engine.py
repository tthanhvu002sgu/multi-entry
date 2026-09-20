import pytest
from app.engine import Plan, calculate
from app.broker import DemoBroker


def plan(**changes):
    return Plan(**({'symbol':'EURUSD','entry':1.1,'stop':1.09,'count':4,'budget':60} | changes))


def test_budget_example():
    result = calculate(plan(), DemoBroker())
    assert [r['entry'] for r in result['entries']] == [1.1,1.0975,1.095,1.0925]
    assert result['lot_each'] == .02
    assert result['total_loss'] == pytest.approx(50)


def test_fixed_example():
    assert calculate(plan(sizing='fixed',lot=.01),DemoBroker())['total_loss'] == pytest.approx(25)


def test_exact_budget():
    assert calculate(plan(budget=25),DemoBroker())['feasible']
    assert calculate(plan(budget=50),DemoBroker())['lot_each'] == .02


def test_sell_symmetry():
    result = calculate(plan(side='sell',entry=1.09,stop=1.10),DemoBroker())
    assert result['total_loss'] == pytest.approx(50)
    assert result['entries'][1]['entry'] == 1.0925


def test_minimum_infeasible():
    result = calculate(plan(budget=20),DemoBroker())
    assert not result['feasible'] and not result['within_budget']
    assert result['minimum_risk'] == pytest.approx(25)


def test_fees_and_reserve_reduce_lot():
    result = calculate(plan(budget=55,commission=7,reserve=5),DemoBroker())
    assert result['lot_each'] == .01
    assert result['total_loss'] == pytest.approx(30.28)


@pytest.mark.parametrize('changes',[{'stop':1.2},{'entry':1.09,'stop':1.09001,'side':'sell','count':4},{'sizing':'fixed','lot':.015}])
def test_invalid_plan(changes):
    with pytest.raises(ValueError): calculate(plan(**changes),DemoBroker())


def test_single_entry():
    result = calculate(plan(count=1,budget=60),DemoBroker())
    assert result['lot_each'] == .06


def test_all_counts_budget_invariant():
    for count in (1,2,3,7,25,50):
        for budget in (1,25,50,100,103.73):
            result = calculate(plan(count=count,budget=budget),DemoBroker())
            if result['feasible']:
                assert result['total_loss'] <= budget + 1e-8
                assert result['lot_each'] >= .01


def test_account_change_rejects_result():
    class Changed(DemoBroker):
        def verify_account(self,account): raise ValueError('account changed')
    with pytest.raises(ValueError,match='account changed'): calculate(plan(),Changed())
