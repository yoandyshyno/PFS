'''
Created on 15.08.2011

@author: jens
'''

import logging
import multiprocessing
import Queue
import time
import threading

from photofilmstrip.lib.common.Singleton import Singleton
from photofilmstrip.lib.jobimpl.IVisualJobManager import IVisualJobManager
from photofilmstrip.lib.jobimpl.LogVisualJobManager import LogVisualJobManager
from photofilmstrip.lib.jobimpl.Worker import Worker



class JobManager(Singleton):
    
    DEFAULT_CTXGROUP_ID = "general"
    
    def __init__(self):
        self.__defaultVisual = LogVisualJobManager()
        self.__visuals = [self.__defaultVisual]
        
        self.__destroying = False
        self.__worker = []

        self.__jobCtxIdleLock = threading.Lock()
        self.__jobCtxsIdle = {}
        
        self.__jobCtxActiveLock = threading.Lock()
        self.__jobCtxsActive = {}
        
        self.__logger = logging.getLogger("JobManager")
        
    def AddVisual(self, visual):
        assert isinstance(visual, IVisualJobManager)
        if self.__defaultVisual in self.__visuals:
            self.__visuals.remove(self.__defaultVisual)

        self.__visuals.append(visual)
    
    def RemoveVisual(self, visual):
        if visual in self.__visuals:
            self.__visuals.remove(visual)
        
        if len(self.__visuals) == 0:
            self.__visuals.append(self.__defaultVisual)
        
    def Init(self, workerCtxGroup=None, workerCount=None):
        if workerCtxGroup is None:
            workerCtxGroup = JobManager.DEFAULT_CTXGROUP_ID
        if workerCount is None:
            workerCount = multiprocessing.cpu_count()
            
        # initialize queues
        with self.__jobCtxIdleLock:
            if not self.__jobCtxsIdle.has_key(workerCtxGroup):
                self.__jobCtxsIdle[workerCtxGroup] = Queue.Queue()
        with self.__jobCtxActiveLock:
            if not self.__jobCtxsActive.has_key(workerCtxGroup):
                self.__jobCtxsActive[workerCtxGroup] = None
        
        newWorkers = []
        i = 0
        while i < workerCount:
            self.__logger.debug("creating worker for group %s", workerCtxGroup)
            worker = Worker(self, workerCtxGroup, i)
            newWorkers.append(worker)
                            
            i += 1
            
        for worker in newWorkers:
            self.__worker.append(worker)
            worker.start()

    def EnqueueContext(self, jobContext):
        assert isinstance(threading.current_thread(), threading._MainThread)
        
        if not self.__jobCtxsIdle.has_key(jobContext.GetGroupId()):
            raise RuntimeError("no worker for job group available") 

        self.__logger.debug("%s: register job", jobContext)
        
        self.__jobCtxsIdle[jobContext.GetGroupId()].put(jobContext)

        for visual in self.__visuals:
            visual.RegisterJob(jobContext)

    def _GetWorkLoad(self, workerCtxGroup, block=True, timeout=0.1):
        jcIdleQueue = self.__jobCtxsIdle[workerCtxGroup]
        
        jobCtxActive = self.__jobCtxsActive[workerCtxGroup]
        while jobCtxActive is None:
            # no context active, get one from idle queue
            jcIdle = jcIdleQueue.get(True, 1)
            if self.__StartCtx(jcIdle):
                jobCtxActive = jcIdle
                self.__jobCtxsActive[workerCtxGroup] = jobCtxActive
    
        try:
            return jobCtxActive, jobCtxActive.GetWorkLoad(block, timeout) # FIXME: no tuple
        except Queue.Empty:
            # FIXME: Erst beenden, wenn alle Worker fertig sind
            result = True
            while not self.__destroying:
                for worker in self.__worker[:]: 
                    # use copy because this list is not thread safe
                    if worker.GetContextGroupId() == workerCtxGroup:
                        result = result and not worker.IsBusy()
                if result:
                    break
                else:
                    time.sleep(0.5)
                
            jobCtxActive = None
            with self.__jobCtxActiveLock:
                jobCtxActive = self.__jobCtxsActive[workerCtxGroup]
                self.__jobCtxsActive[workerCtxGroup] = None
            if jobCtxActive is not None:
                self.__FinishCtx(jobCtxActive)
            
            raise Queue.Empty()

    def __StartCtx(self, ctx):
        self.__logger.debug("starting %s...", ctx.GetName())
        try:
            ctx.Begin()
        except:
            self.__logger.error("not started %s", ctx.GetName(), exc_info=1)
            return False            

        self.__logger.debug("started %s", ctx.GetName())
        return True
    
    def __FinishCtx(self, ctx):
        self.__logger.debug("finalizing %s...", ctx.GetName())
        try:
            ctx.Finalize()
        except:
            self.__logger.error("error %s", ctx.GetName(), exc_info=1)
        finally:
            self.__logger.debug("finished %s", ctx.GetName())
            
#    def Abort(self, jobContext):
#        self.__logger.debug("%s: aborting...", jobContext.GetName())
#        
#        # the workers should stop processing tasks
#        jobContext.Pause()
#        
#        # set the abort flag in the progress handler
#        jobContext.Abort()
#        
#        while 1:
#            self.__logger.debug("%s: emptying task queue", jobContext.GetName())
#            try:
#                jobContext.GetTaskQueue().get(True, 0.05)
#            except Queue.Empty:
#                self.__logger.debug("%s: task queue empty", jobContext.GetName())
#                break
#            
#        # release the pause loop in the workers
#        jobContext.Resume()
#            
#        # wait for workers to terminate
#        for tw in jobContext.GetWorkers():
#            self.__logger.debug("%s: joining worker: %s", jobContext.GetName(), tw)
#            tw.join(3.0)
#            if tw.is_alive():
#                self.__logger.debug("%s: killing worker: %s", jobContext.GetName(), tw)
#                tw.terminate()
                
    def Destroy(self):
        self.__destroying = True
        self.__logger.debug("start destroying")
        for worker in self.__worker:
            worker.Kill()

        for worker in self.__worker:
            self.__logger.debug("joining worker %s", worker.getName())
            worker.join(3)
            if worker.isAlive():
                self.__logger.warning("could not join worker %s", worker.getName())

        self.__logger.debug("destroyed")

