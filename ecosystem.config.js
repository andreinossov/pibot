module.exports = {
    apps: [
        {
            name: "pikalo-bot",
            script: "bot.py",
            interpreter: "./venv/bin/python3",
            restart_delay: 5000,
            autorestart: true,
            max_memory_restart: "200M",

            error_file: "logs/error.log",
            out_file: "logs/out.log",
            merge_logs: true,
            env: {
                PYTHONUNBUFFERED: "1"
            }
        }
    ]
};
