const express = require('express');
const fs = require('fs');
const path = require('path');

const app = express();
const port = 3001;

const logsDir = path.join(__dirname, 'logs');

// Provide a JSON index of available logs
app.get('/logs/index.json', (req, res) => {
  fs.readdir(logsDir, (err, files) => {
    if (err) {
      res.status(500).json({ error: 'Unable to read logs directory' });
      return;
    }
    const logs = files
      .filter((file) => file.endsWith('.json'))
      .sort();
    res.json(logs);
  });
});

// Serve the logs directory as static files
app.use('/logs', express.static(logsDir));

app.listen(port, () => {
  console.log(`Server running at http://localhost:${port}`);
});