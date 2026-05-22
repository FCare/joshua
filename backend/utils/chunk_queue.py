import threading
import asyncio
import queue
import inspect
import time

class ChunkQueue(queue.PriorityQueue):
    def wrapper_targetFuncSync(self, f):
        print(f"🐛 ChunkQueue: Worker thread started for handler {f}")
        while not self.is_running.is_set():
            try:
                item = self.get(timeout=1)  #Wait for a chunk
                priority, timestamp, counter, chunk = item
                print(f"🐛 ChunkQueue: Worker got message: {type(chunk).__name__}")
                f(chunk)
                print(f"🐛 ChunkQueue: Handler completed for: {type(chunk).__name__}")
                self.task_done() #Trigger that it is done
            except queue.Empty:
                continue
            except Exception as e:
                print(f"🐛 ChunkQueue: Worker exception: {e}")
                import traceback
                print(f"🐛 ChunkQueue: Traceback: {traceback.format_exc()}")
                self.task_done()

    def wrapper_targetFuncAsync(self, f, looper):
        async def wait_for_f(chunk):
            await f(chunk)
        
        # Crée sa propre boucle si aucune n'est fournie
        if looper is None:
            looper = asyncio.new_event_loop()
            asyncio.set_event_loop(looper)
        
        while not self.is_running.is_set():
            try:
                item = self.get(timeout=1)  #Wait for a chunk
                priority, timestamp, counter, chunk = item
                try:
                    if looper.is_running():
                        future = asyncio.run_coroutine_threadsafe(f(chunk), looper)
                        result = future.result()
                    else:
                        # Si la boucle n'est pas active, exécute directement
                        looper.run_until_complete(f(chunk))
                except Exception as e:
                    # Log error but continue processing
                    print(f"🐛 CRITICAL: ChunkQueue handler exception: {e}")
                    import traceback
                    print(f"🐛 CRITICAL: Traceback: {traceback.format_exc()}")
                finally:
                    self.task_done() #Trigger that it is done
            except queue.Empty:
                continue

    def __init__(self, size = 0, handler = None, priority = 2):
        super().__init__(size)
        self.is_running = threading.Event()
        self.is_running.clear()  # Initialise à False pour que les workers démarrent
        self.priority = priority  # Priorité fixe pour cette queue (0=critical, 2=normal)
        self.looper = None
        self._mutex = threading.Lock()
        self._counter = 0  # Compteur pour éviter la comparaison directe d'objets dans PriorityQueue
        if handler is not None:
            if inspect.iscoroutinefunction(handler):
                try:
                    self.looper = asyncio.get_running_loop()
                except RuntimeError:
                    # Pas de loop en cours, en créera un si nécessaire
                    self.looper = None
                threading.Thread(target=self.wrapper_targetFuncAsync,
                             args=[handler, self.looper], daemon=True).start()
            else:
                threading.Thread(target=self.wrapper_targetFuncSync,
                             args=[handler], daemon=True).start()
    def flush(self):
        # Flush until queue is actually empty (no race condition)
        while True:
            try:
                item = self.get(block=False)  # Get item (compatible avec tous les formats)
                self.task_done()
            except queue.Empty:
                break  # Queue is truly empty
    def stop(self):
        self.is_running.set()  # Signal workers to stop
        
        # Give workers time to finish current task
        time.sleep(0.1)
        
        # Now safely flush remaining items
        self.flush()

    def enqueue(self, chunk):
        with self._mutex:
            # Incrémenter le compteur pour éviter la comparaison directe d'objets
            self._counter += 1
                    # Utiliser la priorité du message si disponible, sinon priorité par défaut de la queue
            if hasattr(chunk, 'priority'):
                message_priority = chunk.priority
                print(f"ChunkQueue: Message {type(chunk).__name__} with priority {message_priority}")
            else:
                message_priority = self.priority
                print(f"ChunkQueue: Non-message object {type(chunk).__name__} with default priority {message_priority}")

            # Tuple à 4 éléments : (priorité, timestamp, compteur, chunk)
            # Le compteur garantit qu'aucune comparaison directe d'objets n'aura lieu
            item = (message_priority, time.time(), self._counter, chunk)
            self.put(item)
            # Automatic yield: Allow worker thread to process immediately
            time.sleep(0)  # Force scheduler yield for real-time streaming
