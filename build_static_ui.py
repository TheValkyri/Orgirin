import os
import glob
import subprocess
import logging
import shutil

logger = logging.getLogger(__name__)

QWEBCHANNEL_JS = """
"use strict";

var QWebChannelMessageTypes = {
    signal: 1,
    propertyUpdate: 2,
    init: 3,
    idle: 4,
    debug: 5,
    invokeMethod: 6,
    connectToSignal: 7,
    disconnectFromSignal: 8,
    setProperty: 9,
    response: 10
};

var QWebChannel = function(transport, initCallback) {
    if (typeof transport !== "object" || typeof transport.send !== "function") {
        console.error("The QWebChannel expects a transport object with a send function and onmessage handler.");
        return;
    }

    var channel = this;
    this.transport = transport;
    this.send = function(data) {
        channel.transport.send(JSON.stringify(data));
    };

    this.transport.onmessage = function(message) {
        var data = message.data;
        if (typeof data === "string") {
            data = JSON.parse(data);
        }
        switch (data.type) {
        case QWebChannelMessageTypes.signal:
            channel.handleSignal(data);
            break;
        case QWebChannelMessageTypes.response:
            channel.handleResponse(data);
            break;
        case QWebChannelMessageTypes.propertyUpdate:
            channel.handlePropertyUpdate(data);
            break;
        default:
            console.error("invalid message received:", message.data);
            break;
        }
    };

    this.execCallbacks = {};
    this.execId = 0;
    this.objects = {};

    this.send({type: QWebChannelMessageTypes.idle});

    this.exec({type: QWebChannelMessageTypes.init}, function(data) {
        for (var objectName in data) {
            var object = new QObject(objectName, data[objectName], channel);
        }
        for (var name in channel.objects) {
            channel.objects[name].__initCallbacks__();
        }
        if (initCallback) {
            initCallback(channel);
        }
    });
};

var QObject = function(name, data, webChannel) {
    this.__id__ = name;
    webChannel.objects[name] = this;
    this.__metamethods__ = data.methods || [];
    this.__metasignals__ = data.signals || [];
    this.__metaproperties__ = data.properties || [];

    var self = this;

    this.__initCallbacks__ = function() {
        for (var i = 0; i < self.__metasignals__.length; ++i) {
            var signal = self.__metasignals__[i];
            var signalName = signal[0];
            var signalIndex = signal[1];
            (function(signalIndex, signalName) {
                var connections = [];
                var signalObject = {
                    connect: function(callback) {
                        if (typeof callback !== "function") {
                            console.error("Bad callback given to connect to signal " + signalName);
                            return;
                        }
                        connections.push(callback);
                        if (connections.length === 1) {
                            webChannel.send({
                                type: QWebChannelMessageTypes.connectToSignal,
                                object: self.__id__,
                                signal: signalIndex
                            });
                        }
                    },
                    disconnect: function(callback) {
                        var index = connections.indexOf(callback);
                        if (index !== -1) {
                            connections.splice(index, 1);
                        }
                        if (connections.length === 0) {
                            webChannel.send({
                                type: QWebChannelMessageTypes.disconnectFromSignal,
                                object: self.__id__,
                                signal: signalIndex
                            });
                        }
                    }
                };
                signalObject.__connections__ = connections;
                self[signalName] = signalObject;
                self[signalIndex] = signalObject;
            })(signalIndex, signalName);
        }
    };

    for (var i = 0; i < this.__metamethods__.length; ++i) {
        var method = this.__metamethods__[i];
        var methodName = method[0];
        var methodIndex = method[1];
        (function(methodIndex, methodName) {
            self[methodName] = function() {
                var args = [];
                var callback;
                for (var j = 0; j < arguments.length; ++j) {
                    if (typeof arguments[j] === "function") {
                        callback = arguments[j];
                    } else {
                        args.push(arguments[j]);
                    }
                }
                return new Promise(function(resolve, reject) {
                    webChannel.exec({
                        type: QWebChannelMessageTypes.invokeMethod,
                        object: self.__id__,
                        method: methodIndex,
                        args: args
                    }, function(data) {
                        if (callback) callback(data);
                        resolve(data);
                    });
                });
            };
        })(methodIndex, methodName);
    }
};

QWebChannel.prototype.exec = function(data, callback) {
    if (callback) {
        data.id = ++this.execId;
        this.execCallbacks[data.id] = callback;
    }
    this.send(data);
};

QWebChannel.prototype.handleSignal = function(message) {
    var object = this.objects[message.object];
    if (object && object[message.signal]) {
        var signal = object[message.signal];
        var connections = signal.__connections__ || [];
        for (var i = 0; i < connections.length; ++i) {
            connections[i].apply(null, message.args);
        }
    }
};

QWebChannel.prototype.handleResponse = function(message) {
    if (!message.id) {
        console.error("Invalid response message received: ", message);
        return;
    }
    var callback = this.execCallbacks[message.id];
    if (callback) {
        delete this.execCallbacks[message.id];
        callback(message.data);
    }
};

QWebChannel.prototype.handlePropertyUpdate = function(message) {
    for (var i = 0; i < message.signals.length; ++i) {
        var signal = message.signals[i];
        this.handleSignal(signal);
    }
};

if (typeof window !== "undefined") {
    window.QWebChannel = QWebChannel;
}
"""

def build_static_ui():
    project_dir = os.path.dirname(os.path.abspath(__file__))
    output_public = os.path.join(project_dir, ".output", "public")
    assets_dir = os.path.join(output_public, "assets")
    client_dir = os.path.join(project_dir, "dist", "client")
    
    print("Building UI with npm run build...")
    res = subprocess.run(["npm.cmd" if os.name == "nt" else "npm", "run", "build"], cwd=project_dir, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"npm run build failed:\n{res.stderr}")
        raise RuntimeError("Failed to build UI")
        
    source_assets = os.path.join(client_dir, "assets")
    if not os.path.isdir(source_assets):
        raise RuntimeError("Built client assets missing in dist/client/assets")

    if os.path.isdir(output_public):
        shutil.rmtree(output_public, ignore_errors=True)
    os.makedirs(output_public, exist_ok=True)
    shutil.copytree(source_assets, assets_dir)
    for filename in ("favicon.ico", "robots.txt"):
        source = os.path.join(client_dir, filename)
        if os.path.isfile(source):
            shutil.copy2(source, os.path.join(output_public, filename))

    # Write qwebchannel.js into the static package output.
    qweb_path = os.path.join(output_public, "qwebchannel.js")
    with open(qweb_path, "w", encoding="utf-8") as f:
        f.write(QWEBCHANNEL_JS)
        
    print("Scanning asset files in dist/client/assets...")
    css_files = glob.glob(os.path.join(assets_dir, "*.css"))
    js_files = glob.glob(os.path.join(assets_dir, "index-*.js")) or glob.glob(os.path.join(assets_dir, "*.js"))
    
    if not css_files or not js_files:
        raise RuntimeError("Built CSS or JS files missing in .output/public/assets")
        
    css_name = os.path.basename(css_files[0])
    main_js = [f for f in js_files if "index-" in os.path.basename(f)]
    js_name = os.path.basename(main_js[0]) if main_js else os.path.basename(js_files[0])
    
    index_html_content = f"""<!DOCTYPE html>
<html lang="vi">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Origin — Tải video & audio YouTube chất lượng tốt nhất</title>
    <!-- CRITICAL: DO NOT REMOVE window.$_TSR. Required for TanStack Start static client hydration. -->
    <script>
      window.$_TSR = window.$_TSR || {{
        h: function(fn) {{ return typeof fn === "function" ? fn() : fn; }},
        clean: function() {{}},
        router: {{
          matches: [],
          manifest: {{ routes: {{ __root__: {{ id: "__root__" }} }} }},
          dehydratedData: null,
          lastMatchId: null
        }},
        buffer: [],
        t: null
      }};
    </script>
    <link rel="stylesheet" href="./assets/{css_name}" />
    <script src="./qwebchannel.js"></script>
  </head>
  <body class="dark bg-background text-foreground antialiased">
    <div id="root"></div>
    <script type="module" src="./assets/{js_name}"></script>
  </body>
</html>
"""
    
    index_html_path = os.path.join(output_public, "index.html")
    with open(index_html_path, "w", encoding="utf-8") as f:
        f.write(index_html_content)
        
    print(f"Static index.html generated successfully at: {index_html_path}")

if __name__ == "__main__":
    build_static_ui()
