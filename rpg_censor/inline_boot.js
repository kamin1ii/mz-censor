/* Editable files for literal images embedded in scripts. Installed before plugins. */
(function () {
    'use strict';
    var fs = require('fs'), path = require('path'), crypto = require('crypto');
    var base = path.dirname(process.mainModule.filename);
    var mapping = JSON.parse(fs.readFileSync(path.join(base, 'censor-inline.json'), 'utf8'));
    var descriptor = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'src');
    Object.defineProperty(HTMLImageElement.prototype, 'src', {
        configurable: true, enumerable: descriptor.enumerable, get: descriptor.get,
        set: function (url) {
            var match = typeof url === 'string' && /^data:image\/(png|gif|jpeg|jpg|webp|bmp);base64,([A-Za-z0-9+/=]+)$/i.exec(url);
            if (match) {
                var hash = crypto.createHash('sha256').update(Buffer.from(match[2], 'base64')).digest('hex');
                var relative = mapping[hash];
                if (relative) {
                    if (!/^embedded\/[a-f0-9]{64}\.(png|gif|jpg|webp|bmp)$/.test(relative)) {
                        throw new Error('Invalid embedded image path');
                    }
                    url = 'data:image/' + relative.split('.').pop() + ';base64,' +
                        fs.readFileSync(path.join(base, relative)).toString('base64');
                }
            }
            descriptor.set.call(this, url);
        }
    });
})();
