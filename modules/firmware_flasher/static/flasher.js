// flasher.js
// Firmware flasher implementation

// Configuration
const CONFIG = {
    baudRate: 921600,
    firmwareUrl: '/firmware-flasher/firmware',
    infoUrl: '/firmware-flasher/firmware/info',
    flashOffset: 0x10000,
    // Device filter
    portFilters: [
        { usbVendorId: 0x303a } // Espressif Systems
    ]
};

// State
let port = null;
let esploader = null;
let transport = null;
let ESPLoader = null;
let Transport = null;

// UI Elements
const ui = {
    // Buttons
    btnConnect: document.getElementById('btnConnect'),
    btnDisconnect: document.getElementById('btnDisconnect'),
    btnFlash: document.getElementById('btnFlash'),
    
    // Sections
    deviceDisconnected: document.getElementById('deviceDisconnected'),
    deviceConnected: document.getElementById('deviceConnected'),
    flashSection: document.getElementById('flashSection'),
    
    // Info
    fwFilename: document.getElementById('fwFilename'),
    fwSize: document.getElementById('fwSize'),
    fwChip: document.getElementById('fwChip'),
    fwOffset: document.getElementById('fwOffset'),
    
    // Device Info
    deviceChip: document.getElementById('deviceChip'),
    deviceMac: document.getElementById('deviceMac'),
    
    // Progress & Logs
    logArea: document.getElementById('logArea'),
    progressWrap: document.getElementById('progressWrap'),
    progressFill: document.getElementById('progressFill'),
    progressText: document.getElementById('progressText'),
    statusMsg: document.getElementById('statusMsg')
};

// Terminal output handler
const terminal = {
    clean: () => {
        ui.logArea.innerHTML = '';
    },
    writeLine: (data) => {
        const line = document.createElement('div');
        line.textContent = data;
        ui.logArea.appendChild(line);
        ui.logArea.scrollTop = ui.logArea.scrollHeight;
    },
    write: (data) => {
        const span = document.createElement('span');
        span.textContent = data;
        ui.logArea.appendChild(span);
        ui.logArea.scrollTop = ui.logArea.scrollHeight;
    }
};

// Log message to terminal
function log(msg) {
    terminal.writeLine(`[System] ${msg}`);
}

// Initialize
async function init() {
    log('Initializing Flasher...');
    
    // Check Web Serial
    if (!navigator.serial) {
        document.getElementById('browserWarning').classList.remove('hidden');
        ui.btnConnect.disabled = true;
        log('Error: Web Serial API not supported.');
        return;
    }

    try {
        const response = await fetch(CONFIG.infoUrl);
        if (response.ok) {
            const data = await response.json();
            ui.fwFilename.textContent = data.filename;
            ui.fwSize.textContent = `${(data.size / 1024).toFixed(2)} KB`;
            ui.fwChip.textContent = data.chip;
            ui.fwOffset.textContent = data.offset;
        } else {
            log('Failed to fetch firmware info.');
            document.getElementById('firmwareError').classList.remove('hidden');
        }
    } catch (e) {
        console.error(e);
        log('Error loading firmware info.');
    }

    ui.btnConnect.addEventListener('click', connect);
    ui.btnDisconnect.addEventListener('click', disconnect);
    ui.btnFlash.addEventListener('click', flashFirmware);
}

// Connect to serial device
async function connect() {
    try {
        log('Starting connection process...');
        
        // Import esptool-js
        if (!ESPLoader || !Transport) {
             try {
                log('Importing esptool-js...');
                const module = await import('https://unpkg.com/esptool-js@0.5.6/bundle.js');
                log('Import complete.');
                
                ESPLoader = module.ESPLoader;
                Transport = module.Transport;
                
                // Fallbacks
                if (!ESPLoader && module.default) {
                    ESPLoader = module.default.ESPLoader;
                    Transport = module.default.Transport;
                }
                
                if (!ESPLoader && window.esptool) {
                    ESPLoader = window.esptool.ESPLoader;
                    Transport = window.esptool.Transport;
                }

             } catch (err) {
                 log(`Import error: ${err.message}`);
             }
        }

        // Resolve dependencies
        if (!ESPLoader || !Transport) {
             if (window.esptool) {
                ESPLoader = window.esptool.ESPLoader;
                Transport = window.esptool.Transport;
             }
        }

        if (!ESPLoader || !Transport) {
             throw new Error('Failed to load esptool-js libraries. Check network.');
        }

        log('Requesting port...');
        port = await navigator.serial.requestPort({ filters: CONFIG.portFilters });
        
        // Port management
        log('Port selected (not yet opened).');

        ui.deviceDisconnected.classList.add('hidden');
        ui.deviceConnected.classList.remove('hidden');
        ui.flashSection.classList.remove('hidden');
        
        log('Port opened.');

        log(`Port object: ${port}`);
        log(`Port has getInfo: ${typeof port.getInfo === 'function'}`);
        if (typeof port.getInfo === 'function') {
            try {
                 const info = port.getInfo();
                 log(`Port Info: VID=${info.usbVendorId}, PID=${info.usbProductId}`);
            } catch (err) {
                log(`Error calling port.getInfo(): ${err}`);
            }
        }
        
        log(`Transport type: ${typeof Transport}`);
        log(`ESPLoader type: ${typeof ESPLoader}`);

        log('Initializing Transport...');
        try {
            transport = new Transport(port, true);
            log(`Transport created. Keys: ${Object.keys(transport)}`);
            log(`Transport device: ${transport.device}`);
            
            if (transport.device) {
                 log(`Transport device has getInfo: ${typeof transport.device.getInfo === 'function'}`);
            }
            
            // Polyfill getInfo
            if (!transport.getInfo && transport.device && transport.device.getInfo) {
                log('Monkey-patching getInfo on transport...');
                transport.getInfo = () => transport.device.getInfo();
            }
        } catch (e) {
            log(`Error creating Transport: ${e}`);
            throw e;
        }
        
        log('Initializing ESPLoader...');
        try {
            esploader = new ESPLoader({
                transport: transport,
                baudrate: CONFIG.baudRate,
                terminal: terminal
            });
            log('ESPLoader instance created.');
        } catch (e) {
             log(`Error creating ESPLoader: ${e}`);
             log(`Stack: ${e.stack}`);
             throw e;
        }

        const chip = await esploader.main({ flash_mode: 'dio', flash_freq: '80m' });

        log(`Main returned: ${chip}`);
        if (typeof chip === 'object') {
             log(`Main returned object keys: ${Object.keys(chip)}`);
        }
        
        log(`esploader.chip: ${esploader.chip}`);
        if (esploader.chip) {
             log(`esploader.chip keys: ${Object.keys(esploader.chip)}`);
             log(`esploader.chip constructor: ${esploader.chip.constructor.name}`);
        }

        // Find chip instance
        let chipInstance = null;
        if (chip && typeof chip.getChipDescription === 'function') {
            chipInstance = chip;
        } else if (esploader.chip && typeof esploader.chip.getChipDescription === 'function') {
            chipInstance = esploader.chip;
        }

        if (chipInstance) {
            log('Found chip instance with getChipDescription.');
            ui.deviceChip.textContent = await chipInstance.getChipDescription(esploader);
            ui.deviceMac.textContent = await chipInstance.readMac(esploader);
        } else {
             log('WARNING: Could not find getChipDescription method.');
             
             // Check for snake_case alternatives
             if (esploader.chip && typeof esploader.chip.get_chip_description === 'function') {
                  log('Found get_chip_description (snake_case).');
                  ui.deviceChip.textContent = await esploader.chip.get_chip_description(esploader.loader);
                  ui.deviceMac.textContent = await esploader.chip.get_mac(esploader.loader);
             } else {
                 ui.deviceChip.textContent = "Unknown Chip";
                 ui.deviceMac.textContent = "Unknown MAC";
             }
        }
        
        log(`Connected to ${ui.deviceChip.textContent}`);

    } catch (e) {
        console.error(e);
        log(`Connection failed: ${e.message}`);
        await disconnect();
    }
}

// Disconnect from device
async function disconnect() {
    if (transport) {
        await transport.disconnect();
    }
    if (port) {
        await port.close();
    }
    
    port = null;
    transport = null;
    esploader = null;
    
    ui.deviceConnected.classList.add('hidden');
    ui.flashSection.classList.add('hidden');
    ui.deviceDisconnected.classList.remove('hidden');
    
    ui.deviceChip.textContent = 'Unknown';
    ui.deviceMac.textContent = '--:--:--';
    
    log('Disconnected.');
}

// Flash Firmware
async function flashFirmware() {
    if (!esploader) return;
    
    ui.btnFlash.disabled = true;
    ui.progressWrap.classList.remove('hidden');
    ui.statusMsg.textContent = 'Downloading firmware...';
    
    try {
        // Fetch and convert binary
        const fetchBinaryString = async (name, url) => {
            log(`Downloading ${name}...`);
            const cleanUrl = url + '?t=' + new Date().getTime(); // Cache check
            const fileData = await fetch(cleanUrl).then(r => r.arrayBuffer());
            log(`Loaded ${name}: ${fileData.byteLength} bytes`);
            
            return new Promise((resolve, reject) => {
                const blob = new Blob([fileData]);
                const reader = new FileReader();
                reader.onload = (e) => resolve({ data: e.target.result, name: name });
                reader.onerror = (e) => reject(e);
                reader.readAsBinaryString(blob);
            });
        };

        ui.statusMsg.textContent = 'Downloading firmware...';
        
        // Download all parts
        const [bootloader, partitions, boot_app0, app] = await Promise.all([
            fetchBinaryString('bootloader', `${CONFIG.firmwareUrl}/bootloader`),
            fetchBinaryString('partitions', `${CONFIG.firmwareUrl}/partitions`),
            fetchBinaryString('boot_app0', `${CONFIG.firmwareUrl}/boot_app0`),
            fetchBinaryString('app', CONFIG.firmwareUrl)
        ]);
        
        // Validation
        const computedMD5 = CryptoJS.MD5(CryptoJS.enc.Latin1.parse(app.data)).toString();
        const first256String = app.data.substring(0, 256);
        const first256MD5 = CryptoJS.MD5(CryptoJS.enc.Latin1.parse(first256String)).toString();
        
        log(`Firmware MD5: ${computedMD5}`);
        log(`First 256 bytes MD5: ${first256MD5}`);

        ui.statusMsg.textContent = 'Erasing & Flashing...';

        const fileArray = [
            { data: bootloader.data, address: 0x0000 },
            { data: partitions.data, address: 0x8000 },
            { data: boot_app0.data, address: 0xe000 },
            { data: app.data, address: CONFIG.flashOffset }
        ];

        await esploader.writeFlash({
            fileArray: fileArray,
            flashSize: 'keep', 
            flashMode: 'dio', 
            flashFreq: '80m', 
            eraseAll: false,
            compress: true, 
            reportProgress: (fileIndex, written, total) => {
                const percent = Math.round((written / total) * 100);
                ui.progressFill.style.width = `${percent}%`;
                ui.progressText.textContent = `${percent}%`;
            },
            calculateMD5Hash: (image) => CryptoJS.MD5(CryptoJS.enc.Latin1.parse(image)).toString()
        });
        
        log('Flashing complete!');
        
        // Verification
        ui.statusMsg.textContent = 'Verifying...';
        try {
            const readData = await esploader.readFlash(CONFIG.flashOffset, 256, (i, read, total) => {
                 // ignore progress
            });
            
            let readString = readData;
            if (readData instanceof Uint8Array) {
                readString = Array.from(readData).map(b => String.fromCharCode(b)).join('');
            }
            
            const readMD5 = CryptoJS.MD5(CryptoJS.enc.Latin1.parse(readString)).toString();
            log(`VERIFICATION READ MD5: ${readMD5}`);
            
            if (readMD5 === first256MD5) {
                log('VERIFICATION SUCCESS: Data matches exactly!');
                ui.statusMsg.textContent = 'Success! Verified.';
                ui.statusMsg.style.color = 'var(--neon-success)';
            } else {
                log('VERIFICATION FAILURE: Data mismatch!');
                log(`Expected: ${first256MD5}`);
                log(`Got:      ${readMD5}`);
                ui.statusMsg.textContent = 'Verification Failed!';
                ui.statusMsg.style.color = 'var(--neon-alert)';
            }
            
        } catch (verErr) {
            log(`Verification error: ${verErr.message}`);
        }
        
        ui.statusMsg.textContent = 'Resetting device...';
        
        // Reset sequence
        await transport.setDTR(false);
        await transport.setRTS(true);
        await new Promise(r => setTimeout(r, 100));
        await transport.setDTR(true);
        await transport.setRTS(false);
        await new Promise(r => setTimeout(r, 100));
        await transport.setDTR(false);
        
    } catch (e) {
        console.error(e);
        log(`Flashing error: ${e.message}`);
        ui.statusMsg.textContent = 'Error during flash.';
        ui.statusMsg.style.color = 'var(--neon-alert)';
    } finally {
        ui.btnFlash.disabled = false;
    }
}

// Start
document.addEventListener('DOMContentLoaded', init);
