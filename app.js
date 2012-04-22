#!/usr/bin/env node

var net = require('net');

var wobot = require('wobot');
var request = require('request');

var config = require('./' + process.argv[2]);

var logging = console;

var ignoreMessages = [];

var bot = new wobot.Bot({
      jid: config.jid,
      password: config.password
});

var broadcastsSent = [];

function removeOldBroadcasts() {
    // remove broadcasts sent more than 5 minutes ago
    var cutOff = Date.now() - 5*60*1000;
    var i;
    for(i=broadcastsSent.length - 1; i >= 0; --i) {
        if(broadcastsSent[i] < cutOff) {
            ++i;
            break;
        }
    }
    broadcastsSent.slice(i);
}

function sendBroadcast(user, from, message, color, cb) {
    // make sure you don't send more than 30 broadcasts / 5 minutes
    // (hipchat limits total broadcasts to 100 / 5 minutes)
    removeOldBroadcasts();
    if(broadcastsSent.length + 1 > 30) {
        console.log('returning');
        var msg = 'Your broadcast, `' + from + ': ' + message + '` was not sent,' +
                  ' because the chatroom exceeded its limit of 30 broadcasts' +
                  ' per 5 minutes.';
        bot.message(user, msg);
        return;
    }
    broadcastsSent.push(Date.now());

    request({
        uri: 'https://api.hipchat.com/v1/rooms/message',
        method: 'POST',
        qs: {
            auth_token: config.auth_token,
            room_id: config.room_id,
            from: from,
            message: message,
            color: color
        }
    }, function(err, response, body) {
        if(err) {
            logging.error(err);
            return;
        }
        if(response.statusCode != 200) {
            var obj = JSON.parse(body);
            if(obj.error) {
                cb(obj.error.message);
            } else {
                logging.error('Got unexpected response code: ' + response.statusCode);
                logging.error(body);
            }
            return;
        }

        // ignore this message when it comes up in the chat later
        ignoreMessages.push({
            from: from,
            message: message
        });
        cb();
    });
}

bot.connect();
bot.onConnect(function() {
    bot.join(config.room_name);
});

function send2python(type, data, cb) {
    data.type = type;

    var resp = '';
    var client = net.connect(config.socket, function() {
        client.write(JSON.stringify(data));
        client.write('\n');
    });
    client.setEncoding('utf8');
    client.on('error', function(err) {
        cb(err);
    });
    client.on('data', function(data) {
        resp += data;
        console.log(data);
    });
    client.on('end', function() {
        console.log('disconnected');
        var obj = JSON.parse(resp);
        if(obj.error) {
            return cb(obj.error);
        }
        cb(null, obj.data);
    });
}

bot.onMessage(function(channel, from, message) {
    // ignore messages sent by us via the api
    for(var i=0, l=ignoreMessages.length; i < l; ++i) {
        if(ignoreMessages[i].from === from && ignoreMessages[i].message === message) {
            ignoreMessages.splice(i, 1);
            return;
        }
    }

    console.log('--------------------');
    console.log(channel);
    console.log(from);
    console.log(message);
    var o = {
        name: from,
        message: message
    };
    send2python('message', o, function(err, data) {
        if(err) {
            logging.error(err);
            return;
        }
        processOutput(data);
    });
    console.log('--------------------');
});

function processOutput(data) {
    // broadcasts
    data.broadcasts.forEach(function(broadcast) {
        sendBroadcast(broadcast.botowner,
                      broadcast.name,
                      broadcast.msg,
                      broadcast.color,
                      function(err) {
            if(err) {
                send2python('removebot', {
                    name: broadcast.botname
                }, function(err, rdata) {
                    if(err) {
                        logging.error(err);
                        return;
                    }
                    processOutput(rdata);
                });
            }
        });
    });

    // messages
    data.messages.forEach(function(message) {
        bot.message(config.room_name, message);
    });

    // private messages
    for(var user in data.pms) {
        var pms = data.pms[user];
        pms.forEach(function(message) {
            bot.message(user, message);
        });
    }

    // TODO: timers
}

function parseArgs(str) {
    function isQuote(c) {
        return c === '"' || c === "'";
    }

    // state
    var quoted = false;
    var escaped = false;

    var commands = [''];
    for(var i=0, l=str.length; i < l; ++i) {
        var c = str[i];

        if(isQuote(c) && !escaped) {
            quoted = !quoted;
        } else if(escaped) {
            escaped = false;
            commands[commands.length-1] += c;
        } else if(c === '\\') {
            escaped = true;
        } else if(/\s/.test(c)) {
            if(quoted) {
                commands[commands.length-1] += c;
            } else {
                if(commands[commands.length-1].length !== 0) {
                    commands.push('');
                }
            }
        } else {
            escaped = false;
            commands[commands.length-1] += c;
        }
    }

    return commands;
}

var STATES = {
    ACCEPT_SCRIPT: 0,
    EDIT_SCRIPT: 1,
};

var curState = {};

bot.onPrivateMessage(function(from, message) {
    console.log('--------------------');
    console.log(from);
    console.log(message);
    console.log(curState);

    function reply(msg) {
        bot.message(from, msg);
    }

    if(curState[from]) {
        switch(curState[from].state) {
            case STATES.ACCEPT_SCRIPT:
                if(message === 'abort') {
                    delete curState[from];
                    reply('Aborted.');
                    break;
                }

                send2python('makebot', {
                    user: from,
                    name: curState[from].data,
                    code: message
                }, function(err, resp) {
                    if(err) {
                        reply('ERROR: ' + err.message + '\n' + err.stacktrace + '\n' +
                             'Try entering your script again below, or type `abort`');
                        return;
                    }
                    reply(resp);
                    delete curState[from];
                });
                break;
            case STATES.EDIT_SCRIPT:
                if(message === 'abort') {
                    delete curState[from];
                    reply('Aborted.');
                    break;
                }

                send2python('editbot', {
                    user: from,
                    name: curState[from].data,
                    code: message
                }, function(err, resp) {
                    if(err) {
                        reply('ERROR: ' + err.message + '\n' + err.stacktrace + '\n' +
                             'Try entering your script again below, or type `abort`');
                        return;
                    }
                    reply(resp);
                    delete curState[from];
                });
                break;
        }
        return;
    }

    var args = parseArgs(message);
    function clen(n) {
        return n.length + commands[n][0].length + 1;
    }

    function makeHelp(command) {
        if(command) {
            var data = commands[command];
            return command + ' ' + data[0] + '\n' + data[1] + '\n\n' + data[2];
        }

        var maxLength = 0;
        for(var n in commands) {
            maxLength = Math.max(maxLength, clen(n));
        }

        var help = 'Available Commands:\n';
        for(var n in commands) {
            var spaces = maxLength - clen(n) + 4;
            help += n + ' ' + commands[n][0];
            for(var i=0; i < spaces; ++i) {
                help += ' ';
            }
            help += commands[n][1] + '\n';
        }
        return help;
    }

    var commands = {
        help: [
            '[command]',
            'show help. if command is specified, show detailed help',
            '',
            function() {
                var help = makeHelp(args[1]);
                reply(help);
            }],
        say: [
            'message',
            'say something in the chat',
            '',
            function() {
                bot.message(config.room_name, args[1]);
            }],
        broadcast: [
            'from message [color]',
            'broadcast a message to the chat',
            'from:    name from which the broadcast will be sent\n' +
            'message: the message to broadcast\n' +
            'color:   can be yellow (default), red, green, purple, or random',
            function() {
                if(args.length < 3) {
                    reply('ERROR: `broadcast` requires a `from` and a `message` argument');
                    return;
                } else if(args.length === 3) {
                    args.push(undefined);
                }
                sendBroadcast(from, args[1], args[2], args[3], function(err) {
                    if(err) {
                        reply('ERROR: ' + err);
                    }
                });
            }],
        botcode: [
            'name',
            'view the code for the bot with the given name',
            'to view documentation on the language, type man',
            function() {
                if(args.length < 2) {
                    reply('ERROR: please supply a name for the bot you want to edit');
                    return;
                }

                var name = args[1];

                send2python('botdata', { name: name}, function(err, data) {
                    if(err) {
                        return reply('ERROR: ' + err.message);
                    }

                    reply(data.code);
                });
            }],
        editbot: [
            'name',
            'edit the bot with the given name',
            'to view documentation on the language, type man',
            function() {
                if(args.length < 2) {
                    reply('ERROR: please supply a name for the bot you want to edit');
                    return;
                }

                var name = args[1];

                send2python('botdata', { name: name}, function(err, data) {
                    if(err) {
                        return reply('ERROR: ' + err.message);
                    }

                    curState[from] = {
                        state: STATES.EDIT_SCRIPT,
                        data: name,
                    };
                    reply('Here is the current script:\n' +
                          data.code + '\n' +
                          '-------------------------\n' +
                          'To abort, type `abort` below:\n' +
                          'To edit, send the new script (as a single message) below:');
                });
            }],
        makebot: [
            'name',
            'create a bot with the given name',
            'to view documentation on the language, type man',
            function() {
                if(args.length < 2) {
                    reply('ERROR: please supply a name for the bot you want to make');
                    return;
                }

                var name = args[1];

                if(name.length > 15) {
                    reply('ERROR: bot name must be <= 15 characters in length' +
                          ' (yours was ' + name.length + ')');
                    return;
                }

                send2python('botexists', { name: name}, function(err, exists) {
                    if(err) {
                        return reply('ERROR: ' + err.message);
                    }

                    if(exists) {
                        return reply('A bot named ' + name + ' already exists!');
                    }

                    curState[from] = {
                        state: STATES.ACCEPT_SCRIPT,
                        data: name,
                    };
                    reply('Send your script (as a single message) below:');
                });
            }],
        killbot: [
            'name',
            'delete the bot and all its data',
            '',
            function() {
                if(args.length < 2) {
                    reply('ERROR: please supply a name for the bot you want to kill');
                    return;
                }
                send2python('killbot', {
                    name: args[1]
                }, function(err, resp) {
                    if(err) {
                        reply('ERROR: ' + err.message);
                        return;
                    }
                    reply(resp);
                });
            }],
        listbots: [
            '',
            'list all the bots',
            '',
            function() {
                send2python('listbots', {}, function(err, resp) {
                    if(err) {
                        reply('ERROR: ' + err.message);
                        return;
                    }

                    reply(resp);
                });
            }],
        man: [
            '[function_name]',
            'show documentation about the language and environment',
            'To view detailed documentation for a function, type `man function_name`',
            function() {
                send2python('man', {func: args[1]}, function(err, resp) {
                    if(err) {
                        reply('ERROR: ' + err.message);
                        return;
                    }

                    reply(resp);
                });
            }],
    };

    console.log(args);
    console.log('--------------------');

    var command = commands[args[0]];
    if(!command) {
        resp = 'ERROR: invalid command `' + args[0] + '`\n';
        resp += makeHelp();
        reply(resp);
        return;
    }

    command[3]();
});

process.on('uncaughtException', function(err) {
    logging.error('UNCAUGHT EXCEPTION: ----------------------------');
    logging.error(err);
    logging.error(err.stack);
    logging.error('------------------------------------------------');
});
