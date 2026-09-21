// ============ STARFIELD BACKGROUND ============
const canvas = document.getElementById('starfield');
const ctx = canvas.getContext('2d');
let stars = [];
const STAR_COUNT = 200;

function resizeCanvas() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
}
window.addEventListener('resize', resizeCanvas);
resizeCanvas();

function createStars() {
    stars = [];
    for (let i = 0; i < STAR_COUNT; i++) {
        stars.push({
            x: Math.random() * canvas.width,
            y: Math.random() * canvas.height,
            radius: Math.random() * 1.5 + 0.5,
            speed: Math.random() * 0.3 + 0.1,
            opacity: Math.random() * 0.7 + 0.3,
        });
    }
}
createStars();

function animateStars() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    stars.forEach(star => {
        ctx.beginPath();
        ctx.arc(star.x, star.y, star.radius, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(255, 255, 255, ${star.opacity})`;
        ctx.fill();
        star.y += star.speed;
        if (star.y > canvas.height) {
            star.y = -5;
            star.x = Math.random() * canvas.width;
        }
    });
    requestAnimationFrame(animateStars);
}
animateStars();

// ============ CLOCK & DATE ============
function updateClock() {
    const now = new Date();
    document.getElementById('clock').textContent = now.toLocaleTimeString();
    document.getElementById('date').textContent = now.toLocaleDateString(undefined, {
        weekday: 'long',
        year: 'numeric',
        month: 'long',
        day: 'numeric'
    });
}
setInterval(updateClock, 1000);
updateClock();

// ============ WIDGETS (Battery & Network) ============
async function updateBatteryWidget() {
    const batteryEl = document.getElementById('batteryWidget');
    try {
        if ('getBattery' in navigator) {
            const battery = await navigator.getBattery();
            const updateBattery = () => {
                const level = Math.round(battery.level * 100);
                batteryEl.textContent = `${level}% ${battery.charging ? '⚡' : ''}`;
            };
            updateBattery();
            battery.addEventListener('levelchange', updateBattery);
            battery.addEventListener('chargingchange', updateBattery);
        } else {
            batteryEl.textContent = 'N/A';
        }
    } catch (e) {
        batteryEl.textContent = 'N/A';
    }
}

function updateNetworkWidget() {
    const networkEl = document.getElementById('networkWidget');
    networkEl.textContent = navigator.onLine ? 'Online' : 'Offline';
    window.addEventListener('online', () => networkEl.textContent = 'Online');
    window.addEventListener('offline', () => networkEl.textContent = 'Offline');
}

// ============ WEATHER (Open-Meteo, no API key) ============
async function fetchWeather() {
    const weatherEl = document.getElementById('weatherWidget');
    if (!navigator.geolocation) {
        weatherEl.textContent = 'Geo not supported';
        return;
    }
    navigator.geolocation.getCurrentPosition(async (pos) => {
        try {
            const { latitude, longitude } = pos.coords;
            const response = await fetch(`https://api.open-meteo.com/v1/forecast?latitude=${latitude}&longitude=${longitude}&current_weather=true`);
            const data = await response.json();
            const temp = data.current_weather.temperature;
            const code = data.current_weather.weathercode;
            const desc = getWeatherDescription(code);
            weatherEl.textContent = `${temp}°C ${desc}`;
        } catch (err) {
            weatherEl.textContent = 'Weather unavailable';
        }
    }, () => {
        weatherEl.textContent = 'Location denied';
    });
}

function getWeatherDescription(code) {
    const map = {
        0: 'Clear',
        1: 'Mainly clear',
        2: 'Partly cloudy',
        3: 'Overcast',
        45: 'Fog',
        48: 'Rime fog',
        51: 'Light drizzle',
        53: 'Drizzle',
        55: 'Heavy drizzle',
        61: 'Light rain',
        63: 'Rain',
        65: 'Heavy rain',
        71: 'Light snow',
        73: 'Snow',
        75: 'Heavy snow',
        95: 'Thunderstorm',
    };
    return map[code] || 'Unknown';
}

// ============ VOICE RECOGNITION & SYNTHESIS ============
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition = null;
if (SpeechRecognition) {
    recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = false;
    recognition.lang = 'en-US';
    recognition.maxAlternatives = 1;
} else {
    console.warn('Speech Recognition not supported');
}

const synth = window.speechSynthesis;
let isListening = false;   // User wants to listen
let isSpeaking = false;    // System is currently speaking

const micBtn = document.getElementById('micBtn');
const textInput = document.getElementById('textInput');
const sendBtn = document.getElementById('sendBtn');
const outputLog = document.getElementById('outputLog');
const statusText = document.getElementById('statusText');
const orb = document.getElementById('orb');

// ============ THEME MANAGEMENT ============
const themes = [
    { primary: '#00f0ff', secondary: '#ff00e5', bg: '#0a0a1a', glow: '#00f0ff' },
    { primary: '#00ff88', secondary: '#0088ff', bg: '#0a1a0a', glow: '#00ff88' },
    { primary: '#ffaa00', secondary: '#ff4400', bg: '#1a0a0a', glow: '#ffaa00' },
    { primary: '#aa00ff', secondary: '#00ffcc', bg: '#0a0a1a', glow: '#aa00ff' },
];
let themeIndex = 0;

function applyTheme(index) {
    const theme = themes[index % themes.length];
    document.documentElement.style.setProperty('--primary', theme.primary);
    document.documentElement.style.setProperty('--secondary', theme.secondary);
    document.documentElement.style.setProperty('--bg-dark', theme.bg);
    document.documentElement.style.setProperty('--glow-color', theme.glow);
    document.body.style.background = theme.bg;
}

function cycleTheme() {
    themeIndex = (themeIndex + 1) % themes.length;
    applyTheme(themeIndex);
    speak('Theme changed to ' + (themeIndex + 1));
}

// ============ SPEECH SYNTHESIS ============
function speak(text) {
    if (!synth) {
        console.log('Speech synthesis not supported');
        return;
    }
    // Cancel any ongoing speech
    synth.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 1.0;
    utterance.pitch = 1.0;
    utterance.volume = 1;
    utterance.onstart = () => {
        isSpeaking = true;
        orb.classList.add('active');
        statusText.textContent = 'Speaking...';
        if (recognition && isListening) {
            recognition.stop(); // Pause recognition while speaking
        }
    };
    utterance.onend = () => {
        isSpeaking = false;
        orb.classList.remove('active');
        statusText.textContent = isListening ? 'Listening...' : 'System Online';
        if (isListening && recognition) {
            try {
                recognition.start();
            } catch (e) {
                // Ignore errors if already started
            }
        }
    };
    utterance.onerror = () => {
        isSpeaking = false;
        orb.classList.remove('active');
        statusText.textContent = isListening ? 'Listening...' : 'System Online';
        if (isListening && recognition) {
            try { recognition.start(); } catch (e) {}
        }
    };
    synth.speak(utterance);
}

// ============ LOGGING ============
function addLog(entry, type = 'system') {
    const div = document.createElement('div');
    div.className = `log-entry ${type}`;
    div.textContent = entry;
    outputLog.appendChild(div);
    outputLog.scrollTop = outputLog.scrollHeight;
}

// ============ COMMAND PROCESSING ============
async function processCommand(command) {
    const lower = command.toLowerCase().trim();
    addLog(command, 'user');
    
    // Handle empty
    if (!lower) return;

    // Greetings
    if (/^(hello|hi|hey|good (morning|afternoon|evening))\b/.test(lower)) {
        const hour = new Date().getHours();
        let greeting = 'Hello';
        if (hour < 12) greeting = 'Good morning';
        else if (hour < 18) greeting = 'Good afternoon';
        else greeting = 'Good evening';
        speak(`${greeting}, I am JARVIS. How can I assist you?`);
        return;
    }

    // Time
    if (lower.includes('time') || lower.includes('clock')) {
        const time = new Date().toLocaleTimeString();
        speak(`The current time is ${time}`);
        return;
    }

    // Date
    if (lower.includes('date') || lower.includes('day') || lower.includes('today')) {
        const date = new Date().toLocaleDateString(undefined, {
            weekday: 'long', year: 'numeric', month: 'long', day: 'numeric'
        });
        speak(`Today is ${date}`);
        return;
    }

    // Open Google
    if (lower.includes('open google')) {
        window.open('https://www.google.com', '_blank');
        speak('Opening Google');
        return;
    }

    // Search for something
    if (lower.startsWith('search for ') || lower.startsWith('google ')) {
        const query = lower.replace(/^(search for |google )/, '').trim();
        if (query) {
            const url = `https://www.google.com/search?q=${encodeURIComponent(query)}`;
            window.open(url, '_blank');
            speak(`Searching Google for ${query}`);
        } else {
            speak('What would you like me to search for?');
        }
        return;
    }

    // Play something (YouTube search)
    if (lower.startsWith('play ')) {
        const query = lower.replace('play ', '').trim();
        if (query) {
            const url = `https://www.youtube.com/results?search_query=${encodeURIComponent(query)}`;
            window.open(url, '_blank');
            speak(`Playing ${query} on YouTube`);
        } else {
            speak('What would you like me to play?');
        }
        return;
    }

    // Joke
    if (lower.includes('joke') || lower.includes('funny')) {
        try {
            const response = await fetch('https://official-joke-api.appspot.com/random_joke');
            const data = await response.json();
            speak(`${data.setup} ... ${data.punchline}`);
        } catch (e) {
            speak('Sorry, I could not fetch a joke at the moment.');
        }
        return;
    }

    // Weather
    if (lower.includes('weather') || lower.includes('temperature')) {
        await fetchWeather();
        speak('Fetching current weather. Check the widget for details.');
        return;
    }

    // System status
    if (lower.includes('system status') || lower.includes('battery') || lower.includes('diagnostics')) {
        let status = 'System status: ';
        if ('getBattery' in navigator) {
            try {
                const battery = await navigator.getBattery();
                status += `Battery at ${Math.round(battery.level * 100)}%${battery.charging ? ' and charging' : ''}. `;
            } catch {}
        }
        status += `Network is ${navigator.onLine ? 'online' : 'offline'}. `;
        status += `Browser: ${navigator.userAgent.split(') ')[0]})`;
        speak(status);
        return;
    }

    // Change theme
    if (lower.includes('change theme') || lower.includes('switch theme') || lower.includes('new look')) {
        cycleTheme();
        return;
    }

    // Stop listening
    if (lower.includes('stop listening') || lower.includes('sleep') || lower.includes('go to sleep')) {
        stopListening();
        speak('Going to sleep. Click the microphone to wake me.');
        return;
    }

    // Wake up
    if (lower.includes('wake up') || lower.includes('wake') || lower.includes('start listening')) {
        startListening();
        return;
    }

    // Fallback
    speak(`I'm sorry, I didn't understand "${command}". Try asking for time, date, weather, or search.`);
}

// ============ SPEECH RECOGNITION HANDLERS ============
function startListening() {
    if (!recognition) {
        speak('Speech recognition is not supported in this browser.');
        return;
    }
    if (!isListening) {
        isListening = true;
        micBtn.classList.add('listening');
        statusText.textContent = 'Listening...';
        orb.classList.add('active');
        try {
            recognition.start();
        } catch (e) {
            // Already started, ignore
        }
    }
}

function stopListening() {
    if (isListening) {
        isListening = false;
        micBtn.classList.remove('listening');
        if (!isSpeaking) {
            orb.classList.remove('active');
            statusText.textContent = 'System Online';
        }
        try {
            recognition.stop();
        } catch (e) {}
    }
}

if (recognition) {
    recognition.onresult = (event) => {
        const last = event.results.length - 1;
        const transcript = event.results[last][0].transcript.trim();
        if (transcript && !isSpeaking) {
            processCommand(transcript);
        }
    };

    recognition.onerror = (event) => {
        console.error('Recognition error:', event.error);
        if (event.error === 'not-allowed') {
            stopListening();
            speak('Microphone access denied. Please allow microphone to use voice commands.');
        } else if (event.error === 'no-speech') {
            // Silently restart if still listening
            if (isListening) {
                try { recognition.start(); } catch (e) {}
            }
        }
    };

    recognition.onend = () => {
        // If still supposed to listen and not speaking, restart
        if (isListening && !isSpeaking) {
            try {
                recognition.start();
            } catch (e) {
                // Avoid overlapping starts
            }
        }
    };
}

// ============ EVENT LISTENERS ============
micBtn.addEventListener('click', () => {
    if (isListening) {
        stopListening();
    } else {
        startListening();
    }
});

sendBtn.addEventListener('click', () => {
    const text = textInput.value.trim();
    if (text) {
        processCommand(text);
        textInput.value = '';
    }
});

textInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
        sendBtn.click();
    }
});

// Orb click toggles listening
orb.addEventListener('click', () => {
    if (isListening) {
        stopListening();
    } else {
        startListening();
    }
});

// ============ INITIALIZATION ============
updateBatteryWidget();
updateNetworkWidget();
applyTheme(0);

// Log that system is ready
addLog('System ready. Speak or type a command.', 'system');