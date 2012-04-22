#!/usr/bin/env python

import socket
import os
import json
import SocketServer
import time
import traceback
import copy
import sys

import requests
import pymongo
from sandbox import Sandbox
from sandbox import SandboxConfig

from dsl import DSL
from dsl import Output
import dsl
with open(os.path.splitext(dsl.__file__)[0] + '.py') as f:
    dslcode = f.read()

with open(sys.argv[1]) as f:
    config = json.load(f)

# configure the sandbox
features = [
    'regex',
    'help',
    'time',
    'datetime',
    'itertools',
    'random',
    'hashlib',
    'codecs',
    'encodings',
#   'stdout' # TODO: remove
]
cfg = SandboxConfig(*features)
cfg.timeout = 1
cfg.allowSafeModule('json', 'loads', 'dumps')
cfg.allowSafeModule('copy', 'copy', 'deepcopy')
cfg.max_memory = 100
sandbox = Sandbox(cfg)


connection = pymongo.Connection("localhost", 27017)
db = getattr(connection, config['db'])

db.bots.ensure_index('name', unique=True)
db.botdata.ensure_index('botname', unique=True)

ADDR = config['socket']

def getdocsummary(obj):
    docstring = obj.__doc__
    if not docstring:
        return ''
    return docstring.split('\n', 1)[0].strip()

def getfuncsig(func):
    name = func.func_code.co_name
    args = func.func_code.co_varnames[:func.func_code.co_argcount]
    return '{0}({1})'.format(name, ', '.join(args[1:]))

def getfuncdoc(func):
    sig = getfuncsig(func)
    doc = getdoc(func)

    return '{0}    {1}'.format(sig, doc)

def getdoc(obj):
    docstring = obj.__doc__
    if not docstring:
        return ''
    # Convert tabs to spaces (following the normal Python rules)
    # and split into a list of lines:
    lines = docstring.expandtabs().splitlines()
    # Determine minimum indentation (first line doesn't count):
    indent = sys.maxint
    for line in lines[1:]:
        stripped = line.lstrip()
        if stripped:
            indent = min(indent, len(line) - len(stripped))
    # Remove indentation (first line is special):
    trimmed = [lines[0].strip()]
    if indent < sys.maxint:
        for line in lines[1:]:
            trimmed.append(line[indent:].rstrip())
    # Strip off trailing and leading blank lines:
    while trimmed and not trimmed[-1]:
        trimmed.pop()
    while trimmed and not trimmed[0]:
        trimmed.pop(0)
    # Return a single string:
    return '\n'.join(trimmed)

def sandboxed(func):
    return lambda *args, **kwargs: sandbox.call(func, *args, **kwargs)

@sandboxed
def rundsl(code, dslcode, botname, botowner, data='{}', func=None, args='[]', curl='{}'):
    import json
    args = json.loads(args)
    data = json.loads(data)
    curl = json.loads(curl)
    env = {}
    dsld = {}
    exec(code, env)
    exec(dslcode, dsld)
    DSL = dsld['DSL']
    dsl = DSL(botname, botowner, data, curl)
    dsl._addtoenv(env)

    if func is not None:
        env[func](*args)

        ret = {
            'output': dsl.output.serialize(),
            'data': data,
        }

        r = json.dumps(ret)
        return r
    else:
        # validate that everything is kosher
        if not dsl.output.timers and 'onMessage' not in env:
            raise RuntimeError('You must either define an onMessage(name, message) function or have at least 1 delayed or periodic function!')


class Env(DSL):
    def __init__(self, code, botname, botowner):
        self._botname = botname
        self._botowner = botowner
        self._code = code
        self._data = None
        self._modified = False
        self.output = Output()

    def compileCode(self):
        rundsl(self._code, dslcode, self._botname, self._botowner)

    def loadData(self):
        data = db.botdata.find_one({'botname': self._botname})
        self._data = data['data'] if data else {}

    def saveData(self):
        if not self._modified:
            return # don't do a db query if you don't have to

        db.botdata.update({'botname': self._botname},
                          {
                              'botname': self._botname,
                              'data': self._data,
                          },
                          upsert=True, safe=True)

    def onMessage(self, name, message):
        self.loadData()
        data = json.dumps(self._data)
        args = json.dumps([name, message])
        curlcount = 0
        print '=----------------------======'
        curldata = {}
        while curlcount < 4:
            try:
                curls = json.dumps(curldata)
                resp = rundsl(self._code,
                              dslcode,
                              self._botname,
                              self._botowner,
                              data,
                              'onMessage',
                              args,
                              curls)
                break
            except UserWarning as e:
                msg = str(e)
                if msg.startswith('CURLEXCEPTION: '):
                    url = msg[len('CURLEXCEPTION: '):]

                    # curl the url and put the result into curldata
                    curldata[url] = requests.get(url).text

                    curlcount += 1
                    if curlcount == 4:
                        raise RuntimeError('You can only call curl 3 times!')
                else:
                    raise
        print '=----------------------======'

        ret = json.loads(resp)
        data = ret['data']
        output = ret['output']
        if self._data != data:
            self._data = data
            self._modified = True
        self.output.parse(output)
        self.saveData()

class BotList(object):
    def add(self, name, user, code):
        dbobj = {
            'name': name,
            'code': code,
            'user': user,
            'createdon': time.time(),
            'lastupdate': time.time(),
            'lastsaid': '',
        }

        try:
            db.bots.save(dbobj, safe=True)
        except pymongo.errors.DuplicateKeyError as e:
            raise RuntimeError('A bot named `{0}` already exists!'.format(name))

    def edit(self, name, user, code):
        bot = db.bots.find_one({'name': name})
        if bot is None:
            raise RuntimeError('Bot `{0}` does not exist!'.format(name))
        bot['code'] = code
        bot['user'] = user
        bot['lastupdate'] = time.time()
        db.bots.update({'name': name}, bot, safe=True)

    def update_lastsaid(self, name, lastsaid):
        # if nothing was said, don't update lastsaid! (duh)
        if not len(lastsaid):
            return

        # don't need safe mode because it's not THAT critical
        db.bots.update({'name': name}, {'$set': {'lastsaid': lastsaid}})

    def remove(self, name):
        bot = db.bots.find_one({'name': name})
        if bot is None:
            raise RuntimeError('Bot `{0}` does not exist!'.format(name))
        db.bots.remove(bot, safe=True)
        db.botdata.remove({'botname': name}, safe=True)

    def get(self, name):
        return db.bots.find_one({'name': name})

    def all(self):
        return list(db.bots.find())

bots = BotList()

class Handler(SocketServer.StreamRequestHandler):
    def _botdata(self, req):
        name = req['name']
        bot = db.bots.find_one({'name': name})
        if not bot:
            raise RuntimeError('Bot `{0}` does not exist!'.format(name))
        del bot['_id']
        return bot

    def _botexists(self, req):
        name = req['name']
        return db.bots.find_one({'name': name}) is not None

    def _makebot(self, req):
        name = req['name']
        code = req['code']
        user = req['user']

        # see if there are any compile-time errors
        env = Env(code, name, user)
        env.compileCode()

        bots.add(name=name, user=user, code=code)

        return 'Created `{0}` bot'.format(name)

    def _editbot(self, req):
        name = req['name']
        code = req['code']
        user = req['user']

        # see if there are any compile-time errors
        env = Env(code, name, user)
        env.compileCode()

        bots.edit(name=name, user=user, code=code)

        return 'Edited `{0}` bot successfully'.format(name)

    def _killbot(self, req):
        name = req['name']
        bots.remove(name)
        return 'Bot `{0}` has been killed'.format(name)

    def _listbots(self, req):
        bots = list(db.bots.find())
        botnames = [bot['name'] for bot in bots]
        maxlen = max(len(n) for n in botnames)
        maxlen = max(maxlen, len('Bot Name'))
        s = 'Bot Name{0}Last Thing It Said\n'.format(' '*(maxlen - len('Bot Name') + 4))
        s += '{0}\n'.format('-'*len(s))
        for bot in bots:
            name = bot['name']
            lastsaid = bot['lastsaid']
            s += '{0}{1}{2}\n'.format(name, ' '*(maxlen - len(name) + 4), lastsaid)

        return s

    def _message(self, req):
        output = Output()

        for bot in bots.all():
            code = bot['code']
            try:
                env = Env(code, bot['name'], bot['user'])
                env.onMessage(req['name'], req['message'])
                bots.update_lastsaid(bot['name'], env.output.lastsaid)
                output.combine(env.output)
            except Exception as e:
                res = self._removebot(bot, e)
                output.pms.update(res['pms'])
                continue
        return output.serialize()

    # TODO: factor out non-request specific code into a separate function
    def _removebot(self, req, e=None):
        name = req['name']
        bot = bots.get(name)
        user = bot['user']
        code = bot['code']

        output = Output()

        # remove the bot
        if e:
            m = 'ERROR in bot `{0}`: {1}\n{2}'.format(name,
                                                      str(e),
                                                      traceback.format_exc())
            output.pms.setdefault(user, []).append(m)

        m = ('Bot `{0}` was killed due to errors.\n' +
             'Here lies its code:\n{1}').format(name, code)
        output.pms[user].append(m)

        bots.remove(name)

        return output.serialize()

    def _man(self, req):
        func = req.get('func')
        if not func:
            # DSL manpage
            doc = getdoc(DSL)
            doc += '\n'
            fnames = [n for n in dir(DSL) if not n.startswith('_')]
            fsigs = [getfuncsig(getattr(DSL, name)) for name in fnames]
            maxlen = max(len(n) for n in fsigs)
            for name, sig in zip(fnames, fsigs):
                summary = getdocsummary(getattr(DSL, name))
                doc += '    {0}{1}{2}\n'.format(sig,
                                                ' '*(maxlen - len(sig) + 4),
                                                summary)
            doc += '\nFor more information on each function, type man FUNCTION_NAME'
            return doc
        else:
            f = getattr(DSL, func, None)
            if not f:
                raise RuntimeError('{0} is not a valid function name'.format(func))

            sig = getfuncsig(f)
            doc = getdoc(f)
            return sig + '    ' + doc

    def handle(self):
        try:
            data = self.rfile.readline()
            req = json.loads(data)
            print '============================'
            print req
            print '============================'
            resp = getattr(self, '_' + req['type'])(req)
            print resp
            print '============================'
            o = {
                'data': resp
            }
        except Exception as e:
            o = {
                'error': {
                    'message': str(e),
                    'stacktrace': traceback.format_exc()
                }
            }
            if hasattr(e, 'removedbot'):
                o['error']['removedbot'] = e.removedbot

        json.dump(o, self.wfile)


class ForkingUnixStreamServer(SocketServer.UnixStreamServer,
                              SocketServer.ForkingMixIn):
    pass

try:
    os.remove(ADDR)
except OSError:
    pass
server = ForkingUnixStreamServer(ADDR, Handler)
server.serve_forever()
