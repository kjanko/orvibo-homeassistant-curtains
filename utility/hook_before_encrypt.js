/**
 * Hook the JSON BEFORE native encryption in libHomeMate_Security.so
 * Since AES is done in native code, we hook:
 * 1. The JNI bridge methods that pass data to native
 * 2. The native encrypt function itself via Interceptor
 * 3. JSON serialization (the plaintext before encryption)
 *
 * Usage: frida -U -f com.orvibo.homemate -l hook_before_encrypt.js
 */

Java.perform(function() {
    console.log("[*] Hooking pre-encryption layer...\n");

    // Step 1: Find the native security class
    console.log("=== Looking for Security/Encrypt native classes ===");
    var nativeClasses = [];
    Java.enumerateLoadedClasses({
        onMatch: function(name) {
            if (name.indexOf("orvibo") !== -1 &&
                (name.indexOf("Security") !== -1 || name.indexOf("security") !== -1 ||
                 name.indexOf("Encrypt") !== -1 || name.indexOf("encrypt") !== -1 ||
                 name.indexOf("Crypto") !== -1 || name.indexOf("AES") !== -1 ||
                 name.indexOf("aes") !== -1 || name.indexOf("Packet") !== -1 ||
                 name.indexOf("packet") !== -1 || name.indexOf("Codec") !== -1 ||
                 name.indexOf("BaseRes") !== -1 || name.indexOf("core.c") !== -1)) {
                nativeClasses.push(name);
            }
        },
        onComplete: function() {
            nativeClasses.sort();
            nativeClasses.forEach(function(c) { console.log("  " + c); });
        }
    });

    // Step 2: Hook JSON.toString / JSONObject construction
    // The app MUST build JSON before encrypting it
    try {
        var JSONObject = Java.use("org.json.JSONObject");
        JSONObject.toString.overload().implementation = function() {
            var result = this.toString();
            // Only log if it contains Orvibo-related fields
            if (result.indexOf("cmd") !== -1 || result.indexOf("deviceId") !== -1 ||
                result.indexOf("serial") !== -1 || result.indexOf("uid") !== -1 ||
                result.indexOf("curtain") !== -1 || result.indexOf("property") !== -1 ||
                result.indexOf("order") !== -1) {
                console.log("\n[JSON] " + result);
            }
            return result;
        };
        console.log("[+] Hooked JSONObject.toString()");
    } catch(e) { console.log("[-] JSONObject: " + e); }

    // Step 3: Hook String.getBytes (encryption input is usually string→bytes)
    // Too noisy for all strings, but we can filter
    try {
        var String = Java.use("java.lang.String");
        String.getBytes.overload('java.lang.String').implementation = function(charset) {
            var str = this.toString();
            if (str.length > 20 && str.length < 2000 &&
                str.indexOf("{") === 0 &&
                (str.indexOf("cmd") !== -1 || str.indexOf("serial") !== -1)) {
                console.log("\n[STRING→BYTES] " + str);
            }
            return this.getBytes(charset);
        };
        console.log("[+] Hooked String.getBytes(charset)");
    } catch(e) { console.log("[-] String.getBytes: " + e); }

    // Step 4: Hook native library encryption function
    // Look for libHomeMate_Security.so exports
    var modules = Process.enumerateModules();
    modules.forEach(function(mod) {
        if (mod.name.indexOf("HomeMate") !== -1 || mod.name.indexOf("homemate") !== -1 ||
            mod.name.indexOf("Security") !== -1 || mod.name.indexOf("security") !== -1) {
            console.log("\n[MODULE] " + mod.name + " @ " + mod.base);
            var exports = mod.enumerateExports();
            exports.forEach(function(exp) {
                if (exp.name.indexOf("encrypt") !== -1 || exp.name.indexOf("Encrypt") !== -1 ||
                    exp.name.indexOf("crypt") !== -1 || exp.name.indexOf("AES") !== -1 ||
                    exp.name.indexOf("aes") !== -1 || exp.name.indexOf("encode") !== -1 ||
                    exp.name.indexOf("Encode") !== -1 || exp.name.indexOf("Java") !== -1) {
                    console.log("  [EXPORT] " + exp.name + " @ " + exp.address);
                }
            });
        }
    });

    // Step 5: Try to hook the native encrypt function directly
    try {
        var securityModule = Process.findModuleByName("libHomeMate_Security.so");
        if (securityModule) {
            console.log("\n[+] Found libHomeMate_Security.so at " + securityModule.base);
            var exports = securityModule.enumerateExports();
            exports.forEach(function(exp) {
                console.log("  " + exp.type + " " + exp.name);
                // Hook any function that looks like encrypt
                if (exp.name.indexOf("ncrypt") !== -1 || exp.name.indexOf("ncode") !== -1) {
                    Interceptor.attach(exp.address, {
                        onEnter: function(args) {
                            // Try to read the input data
                            console.log("\n[NATIVE " + exp.name + "] called");
                            try {
                                // First arg might be JNIEnv, second might be jclass/jobject
                                // Try reading arg[2] or arg[3] as the data pointer
                                for (var i = 2; i < 6; i++) {
                                    try {
                                        var str = Java.vm.getEnv().getStringUtfChars(args[i]);
                                        if (str && str.indexOf("{") !== -1) {
                                            console.log("[NATIVE INPUT arg" + i + "] " + str);
                                        }
                                    } catch(e) {}
                                    try {
                                        var buf = Memory.readUtf8String(args[i], 500);
                                        if (buf && buf.indexOf("{") !== -1) {
                                            console.log("[NATIVE BUF arg" + i + "] " + buf);
                                        }
                                    } catch(e) {}
                                }
                            } catch(e) {}
                        },
                        onLeave: function(retval) {}
                    });
                }
            });
        }
    } catch(e) { console.log("[-] Native hook: " + e); }

    // Step 6: Hook Gson/Jackson serialization (alternative to JSONObject)
    try {
        var Gson = Java.use("com.google.gson.Gson");
        Gson.toJson.overload('java.lang.Object').implementation = function(obj) {
            var result = this.toJson(obj);
            if (result.indexOf("cmd") !== -1 || result.indexOf("deviceId") !== -1 ||
                result.indexOf("curtain") !== -1 || result.indexOf("order") !== -1) {
                console.log("\n[GSON] " + result);
            }
            return result;
        };
        console.log("[+] Hooked Gson.toJson");
    } catch(e) { console.log("[-] Gson: " + e); }

    // Step 7: Hook the Orvibo packet builder class (from smali: BaseResResp)
    try {
        Java.enumerateLoadedClasses({
            onMatch: function(name) {
                if (name.indexOf("com.orvibo.homemate.core") !== -1) {
                    // Log all core classes to find the right one
                    if (name.split("$").length === 1) { // skip inner classes
                        console.log("[CORE] " + name);
                    }
                }
            },
            onComplete: function() {}
        });
    } catch(e) {}

    console.log("\n[*] Hooks ready. Tap curtain Open/Close/Stop now.");
    console.log("[*] Look for [JSON], [STRING→BYTES], [GSON], or [NATIVE] lines.\n");
});

