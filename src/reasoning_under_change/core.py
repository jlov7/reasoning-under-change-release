"""Independent-clock synthetic dispatch engine with explicit observation dependencies."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ScenarioError(ValueError):
    """Malformed deterministic scenario or invalid state transition."""


@dataclass(frozen=True)
class Event:
    at: int
    entity: str
    kind: str
    value: Any


@dataclass(frozen=True)
class Observation:
    tick: int
    versions: dict[str, int]
    job_id: str
    job_open: bool
    deadline: int
    capacity: int


@dataclass(frozen=True)
class Proposal:
    job_id: str
    made_at: int
    expires_at: int
    read_versions: dict[str, int]
    amount: int = 1


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str


class World:
    def __init__(self, job_id: str, deadline: int, capacity: int, events: list[Event]):
        if type(job_id) is not str or not 1 <= len(job_id) <= 64:
            raise ScenarioError('job_id must be a string of length 1..64')
        if (type(deadline) is not int or not 1 <= deadline <= 1000000
                or type(capacity) is not int or not 0 <= capacity <= 1000000):
            raise ScenarioError('deadline must be an integer in 1..1000000; capacity in 0..1000000')
        if type(events) is not list or len(events) > 10000:
            raise ScenarioError('events must be a list of at most 10000 Event objects')
        for event in events:
            if type(event) is not Event or type(event.at) is not int or not 0 <= event.at <= 1000000:
                raise ScenarioError('event time must be an integer in 0..1000000')
            if event.kind in ('capacity', 'deadline'):
                entity = 'capacity' if event.kind == 'capacity' else f'job:{job_id}'
                if event.entity != entity or type(event.value) is not int or not 0 <= event.value <= 1000000:
                    raise ScenarioError('invalid capacity/deadline event')
            elif event.kind == 'close':
                if event.entity != f'job:{job_id}' or type(event.value) is not bool:
                    raise ScenarioError('invalid close event')
            elif event.kind != 'noise' or event.entity != 'unrelated':
                raise ScenarioError('invalid event kind/entity')
        if any(events[i].at > events[i+1].at for i in range(len(events)-1)):
            raise ScenarioError('events must be pre-sorted by time')
        self.job_id=job_id
        self.tick=0
        self.deadline=deadline
        self.capacity=capacity
        self.job_open=True
        self._events=list(events)
        self._next=0
        self.versions={f'job:{job_id}':0,'capacity':0,'unrelated':0}
        self.applied=[]
        self.accepted=[]
        self._apply_events()

    def _apply_events(self):
        while self._next<len(self._events) and self._events[self._next].at<=self.tick:
            e=self._events[self._next]
            if e.kind=='capacity':
                if e.entity!='capacity' or type(e.value) is not int or e.value<0:raise ScenarioError('bad capacity event')
                self.capacity=e.value
            elif e.kind=='deadline':
                if e.entity!=f'job:{self.job_id}' or type(e.value) is not int or e.value<0:raise ScenarioError('bad deadline event')
                self.deadline=e.value
            elif e.kind=='close':
                if e.entity!=f'job:{self.job_id}' or type(e.value) is not bool:raise ScenarioError('bad close event')
                self.job_open=not e.value
            else:
                if e.entity!='unrelated':raise ScenarioError('bad unrelated event')
            self.versions[e.entity]+=1
            self.applied.append(e)
            self._next+=1

    def advance(self, delta: int):
        if type(delta) is not int or delta < 0:raise ScenarioError('invalid simulated delay')
        self.tick+=delta
        self._apply_events()

    def observe(self) -> Observation:
        return Observation(self.tick,dict(self.versions),self.job_id,self.job_open,self.deadline,self.capacity)

    def act(self, proposal: Proposal) -> str:
        if proposal.job_id!=self.job_id or proposal.amount != 1:
            return 'REJECT_INVALID_PROPOSAL'
        if self.tick>self.deadline or self.tick>proposal.expires_at:
            return 'REJECT_EXPIRED'
        if not self.job_open or self.capacity < proposal.amount:
            return 'REJECT_STALE_STATE'
        self.capacity-=proposal.amount
        self.versions['capacity']+=1
        self.job_open=False
        self.versions[f'job:{self.job_id}']+=1
        self.accepted.append((self.tick,proposal.job_id))
        return 'ACCEPTED'


def propose(obs: Observation) -> Proposal | None:
    if not obs.job_open or obs.capacity < 1 or obs.tick>obs.deadline:
        return None
    return Proposal(obs.job_id,obs.tick,obs.deadline,
                    {f'job:{obs.job_id}':obs.versions[f'job:{obs.job_id}'],
                     'capacity':obs.versions['capacity']})


def evaluate(world: World, proposal: Proposal, policy: str='relevant', ttl: int=1) -> Decision:
    """A relevance rule is not a causal explanation of arbitrary LLM reasoning."""
    if policy not in ('relevant','always','ttl','never'):
        raise ScenarioError('unknown refresh policy')
    if proposal.job_id!=world.job_id:
        return Decision('REFRESH','different_job')
    if policy=='always':return Decision('REFRESH','always')
    if policy=='ttl' and world.tick-proposal.made_at>=ttl:
        return Decision('REFRESH','ttl_elapsed')
    if policy=='relevant':
        required={f'job:{world.job_id}','capacity'}
        if set(proposal.read_versions)!=required:
            return Decision('REFRESH','incomplete_read_set')
        if any(world.versions[key]!=proposal.read_versions[key] for key in required):
            return Decision('REFRESH','relevant_change')
        if world.tick > proposal.expires_at or world.tick > world.deadline:
            return Decision('REFRESH','expired')
    return Decision('ACT','using_existing_plan')


def trial(world: World, delay: int, policy: str, ttl: int=1) -> dict[str, Any]:
    """Run one delayed proposal; refresh recomputes directly with an observation."""
    start=world.observe()
    old=propose(start)
    if old is None:
        return {'outcome':'NO_PROPOSAL','refreshes':0,'first_decision':'NONE','tick':world.tick}
    world.advance(delay)
    d=evaluate(world,old,policy,ttl)
    selected=old
    refreshes=0
    if d.action=='REFRESH':
        refreshes=1
        selected=propose(world.observe())
    outcome=world.act(selected) if selected else 'NO_VALID_ACTION'
    return {'outcome':outcome,'refreshes':refreshes,
            'first_decision':d.reason,'tick':world.tick,
            'applied_events':len(world.applied)}
