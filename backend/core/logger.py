import logging
import os
from logging.handlers import RotatingFileHandler
import sys
import time

# Setup Logs Directory
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) # c:\MacroLens\backend
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)


class WindowsSafeRotatingFileHandler(RotatingFileHandler):
    """
    A RotatingFileHandler that handles Windows file locking issues gracefully.
    If rotation fails due to file being in use, it continues logging without rotation.
    """
    
    def doRollover(self):
        """
        Override doRollover to handle Windows file locking.
        Retry rotation a few times, then give up gracefully if still locked.
        """
        if self.stream:
            self.stream.close()
            self.stream = None
        
        # Attempt rotation with retries
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Standard rotation logic
                if self.backupCount > 0:
                    for i in range(self.backupCount - 1, 0, -1):
                        sfn = self.rotation_filename(f"{self.baseFilename}.{i}")
                        dfn = self.rotation_filename(f"{self.baseFilename}.{i + 1}")
                        if os.path.exists(sfn):
                            if os.path.exists(dfn):
                                os.remove(dfn)
                            os.rename(sfn, dfn)
                    
                    dfn = self.rotation_filename(f"{self.baseFilename}.1")
                    if os.path.exists(dfn):
                        os.remove(dfn)
                    self.rotate(self.baseFilename, dfn)
                
                # Success - break out of retry loop
                break
                
            except PermissionError:
                if attempt < max_retries - 1:
                    time.sleep(0.1)  # Brief pause before retry
                else:
                    # Give up on rotation, just truncate the file if possible to save space
                    try:
                        # Try to truncate instead of rotate
                        with open(self.baseFilename, 'w') as f:
                            f.write('')
                    except PermissionError:
                        # Even truncation failed, just continue without rotation/truncation
                        # The log file will just grow larger for this session
                        pass
            except Exception:
                # Any other error, just continue
                pass
        
        # Reopen the stream
        if not self.delay:
            self.stream = self._open()


def setup_logger(name="backend", log_file="server.log", level=logging.INFO):
    """
    Setup a logger with Console and Rotating File handlers.
    Max size: 10MB, Backups: 5.
    Uses Windows-safe rotation that handles file locking gracefully.
    """
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Avoid adding handlers multiple times
    if not logger.handlers:
        # 1. Console Handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        
        # 2. File Handler (Windows-Safe Rotating)
        file_path = os.path.join(LOG_DIR, log_file)
        try:
            file_handler = WindowsSafeRotatingFileHandler(
                file_path, 
                maxBytes=10*1024*1024, # 10MB
                backupCount=5,
                encoding='utf-8',
                delay=True  # Delay file opening to reduce locking issues
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except Exception as e:
            # Fallback if file handler fails entirely (e.g. permissions)
            print(f"Failed to setup file logging: {e}")
        
    return logger

# Singleton for Root Logger
root_logger = setup_logger("root")
