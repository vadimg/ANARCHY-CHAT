import copy

class Output(object):
    def __init__(self):
        self.broadcasts = []
        self.messages = []
        self.timers = {}
        self.pms = {}
        self.lastsaid = ''

    def combine(self, other):
        self.broadcasts.extend(other.broadcasts)
        self.messages.extend(other.messages)
        self.timers.update(other.timers)
        self.pms.update(other.pms)

    def parse(self, obj):
        self.broadcasts = obj['broadcasts']
        self.messages = obj['messages']
        self.timers = obj['timers']
        self.pms = obj['pms']
        self.lastsaid = obj['lastsaid']

    def serialize(self):
        return {
            'messages': self.messages,
            'broadcasts': self.broadcasts,
            'timers': self.timers,
            'pms': self.pms,
            'lastsaid': self.lastsaid,
        }

class DSL(object):
    """
    To create a script, you must define an onMessage function.
    The function definition is as follows:

        def onMessage(name, message):
            # code goes here

    This function will be called for each message posted in ANARCHY CHAT.
        name: the full name of the person who posted the message
        message: the full message

    You may call functions to post messages in the chat. The functions are:
    """

    def __init__(self, botname, botowner, data, curls):
        self._botname = botname
        self._botowner = botowner
        self.output = Output()
        self.data = data
        self.curls = curls

    def _addtoenv(self, env):
        for name in dir(DSL):
            if name.startswith('_'):
                continue

            def add(name):
                env[name] = lambda *args, **kwargs: getattr(self, name)(*args, **kwargs)
            add(name)

    def _set_lastsaid(self, lastsaid):
        if len(lastsaid) > 50:
            lastsaid = lastsaid[:50] + '...'
        self.output.lastsaid = lastsaid

    def broadcast(self, name, msg, color='yellow'):
        """Send a highlighted broadcast message to the chatroom

        name: name from which the message will be sent (max 15 characters)
        msg: message to send
        color: highlight color. Can be yellow (default), red, green, purple, or random
        """
        name = str(name)
        msg = str(msg)
        self.output.broadcasts.append({
            'name': name,
            'msg': msg,
            'color': str(color),
            'botname': self._botname,
            'botowner': self._botowner,
        })
        self._set_lastsaid('[BROADCAST] {0}: {1}'.format(name, msg))

    def say(self, msg):
        """Make the chatbot say something in the chatroom

        msg: message to send
        """
        msg = str(msg)
        self.output.messages.append(msg)
        self._set_lastsaid(msg)

    def load(self, key):
        """Load data from the database

        key: the unique name of this data
        returns: value
        """
        return self.data.get(key)

    def curl(self, url):
        """Fetch data from THE INTERNET

        url: the URL to fetch data from (must include http://, https://, etc)
        returns: string, containing the body of the HTTP response

        NOTES: You may only call this 3 times per run.
               If you call this twice with the same URL, it will return data
               from the first call.
        """
        url = str(url)
        data = self.curls.get(url)
        if data is None:
            raise UserWarning('CURLEXCEPTION: ' + url)
        return data

    def save(self, key, value):
        """Save data to the database

        key: the unique name of this data (you will use this to load it later)
        value: the data to store
        """
        # deepcopy so that later modifications to value aren't reflected in the db
        self.data[key] = copy.deepcopy(value)

    def periodic(self, minute=None, hour=None, dayofweek=None):
        def decorator(func):
            if func.func_code.co_argcount != 0:
                err = 'Periodic function `{0}` cannot have any arguments'.format(func.func_code.co_name)
                raise CodeError(err, func.func_code.co_firstlineno)

            self.output.timers[func.func_code.co_name] = {
                'minute': minute,
                'hour': hour,
                'dayofweek': dayofweek,
                'func': {
                    'code': func.func_code.co_code,
                    'globals': func.func_globals
                }
            }

            def dontcallmebro():
                raise RuntimeError('Don\'t call periodic functions directly!')
            return dontcallmebro
        return decorator


