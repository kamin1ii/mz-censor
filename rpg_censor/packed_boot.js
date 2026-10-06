/* Local image-mod bridge. The original startup bytes remain available to the
 * packer's environment fingerprint; game.bin and native binaries are untouched. */
(function () {
    'use strict';
    var fs = require('fs'), path = require('path');
    var base = path.dirname(process.mainModule.filename);
    var nativeRead = fs.readFileSync.bind(fs);
    var nativeWrite = fs.writeFileSync.bind(fs);
    var nativeExists = fs.existsSync.bind(fs);
    var original = nativeRead(path.join(base, 'censor-original.html'));
    var indexPath = path.resolve(base, 'index.html').toLowerCase();
    fs.readFileSync = function (file, options) {
        if (typeof file === 'string' && path.resolve(file).toLowerCase() === indexPath) {
            var encoding = typeof options === 'string' ? options : options && options.encoding;
            return encoding ? original.toString(encoding) : Buffer.from(original);
        }
        return nativeRead.apply(null, arguments);
    };
    var requestPath = path.join(base, 'censor-import-request.json');
    var importing = nativeExists(requestPath);
    if (importing) nw.Window.get().hide();
    var toc = null;
    if (importing) {
        var parse = JSON.parse;
        JSON.parse = function () {
            var value = parse.apply(JSON, arguments);
            if (value && value.map && value.dirs && typeof value.map === 'object') toc = value;
            return value;
        };
    }

    // Install after the compiled loader, before its asynchronous image requests.
    window.censorInstall = function () {
        if (importing) {
            try {
                var request = JSON.parse(nativeRead(requestPath, 'utf8'));
                var target = request.output;
                function mkdir(dir) {
                    if (nativeExists(dir)) return;
                    mkdir(path.dirname(dir));
                    fs.mkdirSync(dir);
                }
                if (!toc) throw new Error('Packed table of contents was not captured; cannot certify the image inventory');
                var count = 0, inventory = [], seen = {};
                var crypto = require('crypto');
                var systemBytes = fs.readFileSync(path.join(base, 'data/System.json'));
                var system = JSON.parse(systemBytes.toString('utf8'));
                var inlineImages = {};
                function scanInline(bytes, origin) {
                    var text = bytes.toString('latin1');
                    var regex = /data:image\/(png|gif|jpeg|jpg|webp|bmp);base64,([A-Za-z0-9+/=]+)/g;
                    var match;
                    while ((match = regex.exec(text))) {
                        var decoded = Buffer.from(match[2], 'base64');
                        var digest = crypto.createHash('sha256').update(decoded).digest('hex');
                        var name = 'embedded/' + digest + '.' + (match[1] === 'jpeg' ? 'jpg' : match[1]);
                        if (inlineImages[name]) continue;
                        inlineImages[name] = true;
                        mkdir(path.join(target, 'embedded'));
                        nativeWrite(path.join(target, name), decoded);
                        inventory.push({path: name, size: decoded.length, image: true, embedded: true, origin: origin});
                        count++;
                    }
                }
                function imageSignature(bytes) {
                    if (bytes.slice(0, 8).toString('hex') === '5250474d56000000') {
                        var key = Buffer.from(system.encryptionKey, 'hex');
                        var header = Buffer.from(bytes.slice(16, 32));
                        for (var i = 0; i < 16; i++) header[i] ^= key[i];
                        return header.slice(0, 8).toString('hex') === '89504e470d0a1a0a';
                    }
                    var h = bytes.slice(0, 16).toString('hex');
                    return /^(89504e470d0a1a0a|ffd8ff|474946383761|474946383961|424d|00000100|49492a00|4d4d002a)/.test(h) ||
                           (h.slice(0, 8) === '52494646' && bytes.slice(8, 12).toString() === 'WEBP');
                }
                function walk(relative) {
                    var names = fs.readdirSync(path.join(base, relative));
                    (toc.dirs[relative.normalize('NFC').toLowerCase()] || []).forEach(function (name) {
                        if (names.indexOf(name) < 0) names.push(name);
                    });
                    names.forEach(function (name) {
                        if (name === '.' || name === '..' || /[\\/:]/.test(name)) throw new Error('Invalid virtual filename');
                        var rel = relative ? relative + '/' + name : name;
                        if (seen[rel.toLowerCase()]) return;
                        seen[rel.toLowerCase()] = true;
                        var input = path.join(base, rel);
                        if (fs.statSync(input).isDirectory()) {
                            if (rel !== 'censor-images') walk(rel);
                            return;
                        }
                        var isImage = /\.(png|png_|rpgmvp|jpg|jpeg|webp|bmp|gif|tif|tiff|ico|avif|svg|apng)$/i.test(name);
                        var bytes = null;
                        // Inspect unknown/odd extensions too: this demo contains
                        // an extensionless asset that extension-only scans miss.
                        if (!isImage && !/\.(js|bin|json|ogg|m4a|wav|mp3|rpgmvo|rpgmvm|webm|mp4|ogv|ttf|otf|woff|woff2|html|css)$/i.test(name)) {
                            bytes = fs.readFileSync(input);
                            isImage = imageSignature(bytes);
                        }
                        inventory.push({path: rel, size: fs.statSync(input).size, image: isImage});
                        if (/\.(js|json|css|html|txt)$/i.test(name)) scanInline(bytes || fs.readFileSync(input), rel);
                        if (!isImage) return;
                        var output = path.join(target, rel);
                        mkdir(path.dirname(output));
                        nativeWrite(output, bytes || fs.readFileSync(input));
                        count++;
                    });
                }
                walk('');
                // SecuPacker's readdir(root) omits purely virtual top-level dirs.
                // Seed the traversal from its actual table, not guessed folders.
                Object.keys(toc.dirs).forEach(function (dir) { if (dir) walk(dir); });
                // Literal image data can also live in the compiled startup code.
                var bin = fs.openSync(path.join(base, 'game.bin'), 'r');
                var footer = Buffer.alloc(16);
                fs.readSync(bin, footer, 0, 16, fs.fstatSync(bin).size - 16);
                var length = footer.readUInt32LE(0) + footer.readUInt32LE(4) * 4294967296;
                if (length > 128 * 1024 * 1024) throw new Error('Compiled startup is too large to audit safely');
                var code = Buffer.alloc(length);
                fs.readSync(bin, code, 0, length, 0);
                fs.closeSync(bin);
                scanInline(code, 'game.bin (compiled startup)');
                code = null;
                var accounted = {};
                inventory.forEach(function (entry) {
                    var key = entry.path.normalize('NFC').toLowerCase();
                    accounted[crypto.createHash('sha256').update(key).digest('hex')] = true;
                });
                var omitted = Object.keys(toc.map).filter(function (key) { return !accounted[key]; });
                nativeWrite(path.join(target, 'inventory.json'), JSON.stringify(inventory, null, 2));
                if (omitted.length) throw new Error(omitted.length + ' packed entries were not inventoried; import refused: ' + JSON.stringify(omitted.map(function(k){return {hash:k,meta:toc.map[k]};})));
                mkdir(path.join(target, 'data'));
                nativeWrite(path.join(target, 'data/System.json'), systemBytes);
                nativeWrite(path.join(target, 'inventory.json'), JSON.stringify(inventory, null, 2));
                nativeWrite(request.result, JSON.stringify({count: count, packedEntries: Object.keys(toc.map).length,
                    embeddedImages: Object.keys(inlineImages).length, inventoriedFiles: inventory.length,
                    omitted: omitted.length, engine: Utils.RPGMAKER_NAME, version: Utils.RPGMAKER_VERSION}));
            } catch (error) {
                try { nativeWrite(request.result, JSON.stringify({error: String(error), stack: error.stack})); } catch (_) {}
            }
            nw.App.quit();
            return;
        }
        var mapPath = path.join(base, 'censor-overrides.json');
        if (!nativeExists(mapPath)) return;
        var overrides = JSON.parse(nativeRead(mapPath, 'utf8'));
        function replacement(url) {
            if (typeof url !== 'string' || /^blob:/.test(url)) return url;
            if (/^data:/.test(url)) {
                var match = /^data:image\/(png|gif|jpeg|jpg|webp|bmp);base64,([A-Za-z0-9+/=]+)$/.exec(url);
                if (!match) return url;
                var hash = require('crypto').createHash('sha256').update(Buffer.from(match[2], 'base64')).digest('hex');
                url = 'embedded/' + hash + '.' + (match[1] === 'jpeg' ? 'jpg' : match[1]);
                if (!overrides[url]) return arguments[0];
            }
            var normalized;
            try { normalized = decodeURIComponent(url).replace(/\\/g, '/').split('?')[0]; }
            catch (_) { return url; }
            normalized = normalized.replace(/\.(rpgmvp|png_)$/i, '.png').replace(/^\.\//, '');
            var key = Object.keys(overrides).filter(function (candidate) {
                return normalized === candidate || normalized.slice(-(candidate.length + 1)) === '/' + candidate;
            })[0];
            if (!key) return url;
            var relative = overrides[key];
            if (!relative) return url;
            // Only our own exported PNGs may be named by an override map.
            if (!/^censor-images\/[a-f0-9]+\.png$/.test(relative)) throw new Error('Invalid censor override path');
            return 'data:image/png;base64,' + nativeRead(path.join(base, relative)).toString('base64');
        }
        // MV encrypts before assigning Image.src. Route the bitmap URL first,
        // then exempt only our plaintext data URI from that stock decryptor.
        if (typeof Decrypter !== 'undefined') {
            var check = Decrypter.checkImgIgnore;
            Decrypter.checkImgIgnore = function (url) {
                return /^data:image\/png;base64,/.test(url) || check.call(this, url);
            };
        }
        var load = Bitmap.load;
        Bitmap.load = function (url) { return load.call(this, replacement(url)); };
        var descriptor = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'src');
        Object.defineProperty(HTMLImageElement.prototype, 'src', {
            configurable: true, enumerable: descriptor.enumerable,
            get: descriptor.get,
            set: function (url) { descriptor.set.call(this, replacement(url)); }
        });
        window.censorReplacement = replacement;
    };
})();
