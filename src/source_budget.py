"""One synchronous deadline per employer, shared by listing and detail calls.
SIGALRM enforcement targets the Linux main-thread GitHub runner. Other runtimes
use cooperative checks and explicitly report that enforcement is unavailable.
No worker threads, login automation or background delivery.
"""
import contextvars, logging, signal, threading, time
from contextlib import contextmanager
_CURRENT = contextvars.ContextVar('source_budget', default=None)
logger = logging.getLogger('job_scraper.ats')
DEFAULT_SOURCE_SECONDS = 180.0
class SourceDeadline(BaseException):
    """Not swallowed by generic scraper except Exception handlers."""
class SourceBudget:
    def __init__(self, seconds=DEFAULT_SOURCE_SECONDS):
        self.seconds=float(seconds);self.started=time.monotonic();self.deadline=self.started+self.seconds
        self.candidates=[];self.expired=False;self.detail_skipped=0;self.phase=''
        self.enforced=hasattr(signal,'setitimer') and threading.current_thread() is threading.main_thread()
    def remaining(self):return max(0.0,self.deadline-time.monotonic())
    def remember(self,jobs):
        from src.vacancy_extract import dedupe
        self.candidates=dedupe(self.candidates+list(jobs))
        return jobs
    def call(self,fn,*args,default=None,phase='operation',**kwargs):
        self.phase=phase
        if self.remaining()<=0:
            self.expired=True
            return default
        if not self.enforced:return fn(*args,**kwargs)
        # Preserve any enclosing alarm; never replace an existing watchdog.
        old_timer=signal.getitimer(signal.ITIMER_REAL)
        if old_timer[0]>0:
            logger.warning('Source deadline alarm unavailable: enclosing timer active')
            self.enforced=False
            return fn(*args,**kwargs)
        old_handler=signal.getsignal(signal.SIGALRM)
        def timeout(signum,frame):
            self.expired=True
            # Bound a stuck finally/browser-close during exception unwinding too.
            signal.setitimer(signal.ITIMER_REAL,2.0)
            raise SourceDeadline()
        signal.signal(signal.SIGALRM,timeout)
        signal.setitimer(signal.ITIMER_REAL,max(.001,self.remaining()))
        try:return fn(*args,**kwargs)
        except SourceDeadline:
            logger.warning('Source runtime budget exhausted during %s; retaining %s checkpointed candidates',phase,len(self.candidates))
            return default
        finally:
            signal.setitimer(signal.ITIMER_REAL,0)
            signal.signal(signal.SIGALRM,old_handler)
    def diagnostics(self):
        return dict(limit_seconds=self.seconds,elapsed_seconds=round(time.monotonic()-self.started,2),
                    expired=self.expired,alarm_enforced=self.enforced,detail_requests_skipped=self.detail_skipped,
                    checkpointed_candidates=len(self.candidates),last_phase=self.phase)
@contextmanager
def source_budget(seconds=DEFAULT_SOURCE_SECONDS):
    budget=SourceBudget(seconds);token=_CURRENT.set(budget)
    try:yield budget
    finally:_CURRENT.reset(token)
def current_budget():return _CURRENT.get()
def checkpoint(jobs):
    budget=current_budget()
    if budget:budget.remember(jobs)
    return jobs
def request_timeout(default=20):
    budget=current_budget()
    if budget and budget.remaining()<=0:raise SourceDeadline()
    return max(.001,min(float(default),budget.remaining())) if budget else default
