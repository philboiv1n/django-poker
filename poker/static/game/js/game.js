/* game.js — poker table client logic
 * Config is injected by table.html as window.GAME_CONFIG
 */

let gameId = GAME_CONFIG.gameId;
let username = GAME_CONFIG.username;
let currentTurnUsername = GAME_CONFIG.currentTurnUsername;
let currentPhase = GAME_CONFIG.currentPhase;
let gameStatus = GAME_CONFIG.gameStatus;
let isPlayer = GAME_CONFIG.isPlayer;

let reconnectInterval = 5000;
let displayTime = 1000;

// Global queue to store incoming messages
let messageQueue = [];
let isProcessingQueue = false;


/* -----------------------------------------------------------------------
 * Establishes WebSocket connection to the backend.
 * Handles real-time updates, reconnects, and message parsing.
 * ----------------------------------------------------------------------*/
function connectWebSocket() {

  const wsProtocol = window.location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${wsProtocol}://${window.location.host}/ws/game/${gameId}/`);

  socket.onmessage = function (event) {
    const data = JSON.parse(event.data);
    console.log("🔵 WebSocket Message Received:", data);

    if (data) {

      // Alert user if error detected
      if (data.error !== undefined) {
        showTemporaryMessage(data.error, "error", 1000);
      }

      // Update buttons state
      if (data.type === "update_game_state") {
        gameStatus = data.game_status;
        currentUsername = data.current_username;
        currentPhase = data.current_phase;
        players = data.players;
        buttonsStateMachine(gameStatus, currentPhase, currentUsername, username, isPlayer, players);
      }

      // Update players list
      if (data.players) {
        renderPlayersList(data.players, "players-list");
        isPlayer = data.players.some(player => player.username === username);
      }

      // Update game status
      if (data.game_status !== undefined) {
        document.getElementById("game-status").innerText = data.game_status;
      }

      // Update game phase
      if (data.current_phase !== undefined) {
        document.getElementById("current-phase").innerText = data.current_phase;
      }

      // Update current pot
      if (data.pot !== undefined) {
        num = data.pot.toLocaleString('fr-CA');
        document.getElementById("current-pot").innerText = num;
      }

      // Update community cards
      if (data.community_cards !== undefined) {
        displayCards(data.community_cards, "communityCardsContainer", 5);
      }

      // Display hole cards if available
      if (data.hole_cards && data.hole_cards.length > 0) {
        displayCards(data.hole_cards, "hole-cards", 2);
      }

      // Update total user chips
      if (data.total_user_chips >= 0) {
        num = data.total_user_chips.toLocaleString('fr-CA');
        document.getElementById("total_user_chips").innerText = num;
      }
    }

    if (data.messages && data.messages.length > 0) {
      data.messages.forEach(msg => messageQueue.push(msg));
      processQueue();
    }
  };


  socket.onopen = function () {
    const overlay = document.getElementById("overlay");
    if (overlay) overlay.classList.add("hidden");
  };

  socket.onclose = function (event) {
    console.warn("WebSocket Disconnected. Reconnecting in 5 seconds...");
    const overlay = document.getElementById("overlay");
    const overlayMessage = document.getElementById("overlayMessage");
    if (overlay && overlayMessage) {
      overlayMessage.textContent = "Reconnecting...";
      overlay.classList.remove("hidden");
    }
    setTimeout(connectWebSocket, reconnectInterval);
  };


  socket.onerror = function (error) {
    socket.close();
  };
}


/* -----------------------------------------------------------------------
 * Sends a poker action (fold, call, etc.) to the backend.
 * @param {string} action - Poker action keyword.
 * ----------------------------------------------------------------------*/
function sendAction(action) {
  if (socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ action: action, player: username }));
  } else {
    console.warn("WebSocket is not open.");
  }
}


/* -----------------------------------------------------------------------
 * Sends a join request for the current user via WebSocket.
 * ----------------------------------------------------------------------*/
function sendJoin() {
  if (socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ action: "join", player: username }));
    isPlayer = true;
  } else {
    console.warn("WebSocket is not open.");
  }
}


/* -----------------------------------------------------------------------
 * Sends a leave request for the current user via WebSocket.
 * ----------------------------------------------------------------------*/
function sendLeave() {
  if (!confirm("Are you sure you want to leave the table?")) {
    return;
  }
  if (socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ action: "leave", player: username }));
    document.getElementById("hole-cards").innerText = "";
    isPlayer = false;
  } else {
    console.warn("WebSocket is not open.");
  }
}


/* -----------------------------------------------------------------------
 * Ensures the bet amount is between 1 and 999999999.
 * @param {HTMLInputElement} input - Bet input element.
 * ----------------------------------------------------------------------*/
function validateBetInput(input) {
  if (input.value < 0) {
    input.value = 1;
  } else if (input.value > 999999999) {
    input.value = 999999999;
  }
}


/* -----------------------------------------------------------------------
 * Reads input value and sends a valid bet to the backend.
 * ----------------------------------------------------------------------*/
function placeBet() {
  var betAmount = document.getElementById("bet-amount").value;
  if (betAmount >= 1 && betAmount <= 999999999) {
    sendBet(parseInt(betAmount));
  } else {
    alert("Invalid bet amount! Please enter a value between 1 and 999999.");
  }
}


/* -----------------------------------------------------------------------
 * Sends a bet action with the specified amount.
 * @param {number} amount - Bet amount.
 * ----------------------------------------------------------------------*/
function sendBet(amount) {
  socket.send(JSON.stringify({ action: "bet", player: username, amount: amount }));
}


/* -----------------------------------------------------------------------
 * Renders a list of cards into a container.
 * @param {Array<string>} cards - List of card strings (e.g., ["As", "Kd"]).
 * @param {string} containerId - DOM container ID.
 * @param {number} maxCards - Maximum number of cards to display.
 * ----------------------------------------------------------------------*/
function displayCards(cards, containerId, maxCards = 5) {
  const container = document.getElementById(containerId);
  if (!container) return;

  for (let i = 0; i < maxCards; i++) {
    const slot = container.querySelector(`[data-index="${i}"]`);
    if (!slot) continue;

    if (cards[i]) {
      const rank = cards[i][0] === "T" ? "10" : cards[i][0];
      const suit = cards[i][1];
      let icon = "♠︎";
      let color = "text-black";

      if (suit === "h") {
        icon = "♥︎";
        color = "text-red-500";
      } else if (suit === "d") {
        icon = "♦︎";
        color = "text-red-500";
      } else if (suit === "c") {
        icon = "♣︎";
      }

      slot.className = `card ${color} shadow-xl transition duration-600 transform hover:scale-105`;
      slot.innerText = `${rank}${icon}`;
    } else {
      slot.className = "card-placeholder";
      slot.innerText = "";
    }
  }
}


/* -----------------------------------------------------------------------
 * Parses hidden card data from DOM and renders it visually.
 * ----------------------------------------------------------------------*/
function loadInitialCommunityCards() {
  const communityCardsText = document.getElementById("community-cards").innerText.trim();

  if (communityCardsText.length > 0 && communityCardsText !== "[]") {
    try {
      let communityCardsArray = communityCardsText.replace(/[\[\]']/g, "").split(", ");
      displayCards(communityCardsArray, "communityCardsContainer", 5);
    } catch (error) {
      console.error("Error parsing community cards:", error);
    }
  }
}


/* -----------------------------------------------------------------------
 * Enables/disables action buttons based on game state.
 * @param {boolean} status - True to enable, false to disable.
 * ----------------------------------------------------------------------*/
function enable_action_buttons(status, canCheck = true, canCall = true) {
  const buttons = document.querySelectorAll(".action-button");

  buttons.forEach(button => {
    let shouldEnable = status;

    if (button.id === "check-button" && !canCheck) {
      shouldEnable = false;
    }

    if (button.id === "call-button" && !canCall) {
      shouldEnable = false;
    }

    button.disabled = !shouldEnable;
    button.classList.toggle("bg-gradient-to-r", shouldEnable);
    button.classList.toggle("disabled:bg-gray-600", !shouldEnable);
    button.classList.toggle("disabled:text-gray-400", !shouldEnable);
  });
}


/* -----------------------------------------------------------------------
 * Enables/disables join button.
 * @param {boolean} status - True to enable, false to disable.
 * ----------------------------------------------------------------------*/
function enable_join_button(status) {
  let joinButton = document.getElementById("join-button");

  if (status === true) {
    joinButton.disabled = false;
    joinButton.classList.remove("disabled:bg-gray-600", "disabled:text-gray-400");
  } else {
    joinButton.disabled = true;
    joinButton.classList.add("disabled:bg-gray-600", "disabled:text-gray-400");
  }
}


/* -----------------------------------------------------------------------
 * Enables/disables leave button.
 * @param {boolean} status - True to enable, false to disable.
 * ----------------------------------------------------------------------*/
function enable_leave_button(status) {
  let leaveButton = document.getElementById("leave-button");

  if (status === true) {
    leaveButton.disabled = false;
    leaveButton.classList.remove("disabled:bg-gray-600", "disabled:text-gray-400");
  } else {
    leaveButton.disabled = true;
    leaveButton.classList.add("disabled:bg-gray-600", "disabled:text-gray-400");
  }
}


/* -----------------------------------------------------------------------
 * Central function that manages button visibility and states.
 * @param {string} gameStatus - "waiting", "active", "finished"
 * @param {string} currentPhase - Current game phase
 * @param {string} currentTurnUsername - Who is currently acting
 * @param {string} username - The current user's name
 * @param {boolean} isPlayer - true if user is seated at table
 * @param {Object[]} playersData - players information
 * ----------------------------------------------------------------------*/
function buttonsStateMachine(
  gameStatus,
  currentPhase,
  currentTurnUsername,
  username,
  isPlayer,
  playersData
) {
  // 1) disable everything by default
  enable_action_buttons(false);
  enable_join_button(false);
  enable_leave_button(false);

  // 2) if the game is finished => any seated player can leave
  if (gameStatus === "finished") {
    if (isPlayer === true) {
      enable_leave_button(true);
    }
    return;
  }

  // 3) if we are in showdown => no one can act
  if (currentPhase === "showdown") {
    return;
  }

  // 4) if the game is waiting => non-players can join; players can leave
  if (gameStatus === "waiting") {
    if (isPlayer === false) {
      enable_join_button(true);
    }
    if (isPlayer === true) {
      enable_leave_button(true);
    }
    return;
  }

  // 5) if the game is active => only the current-turn player can act or leave
  if (gameStatus === "active") {
    if (isPlayer === true) {
      if (currentTurnUsername === username) {
        nextToPlay = playersData.find(p => p.is_next_to_play === true);
        enable_action_buttons(true, nextToPlay.user_can_check, nextToPlay.user_can_call);
        enable_leave_button(true);
      }
    }
  }
}


/* -----------------------------------------------------------------------
 * Displays all players and their statuses (dealer, SB, BB, next to act).
 * @param {Array} players - List of player objects from backend
 * @param {string} containerId - ID of the HTML element where to render players
 * ----------------------------------------------------------------------*/
function renderPlayersList(players, containerId = "players-list") {
  const playerList = document.getElementById(containerId);
  if (!playerList) return;

  playerList.innerHTML = "";

  players.forEach(player => {
    const li = document.createElement("li");
    li.setAttribute("data-position", player.position);
    li.setAttribute("class", "flex flex-col items-center p-2 rounded-lg size-36 shadow-lg");

    if (player.username === username) {
      const yourChips = document.getElementById("your-chips");
      if (yourChips) {
        yourChips.textContent = player.game_chips.toLocaleString('fr-CA');
      }
    }

    const dealerMarkup = player.is_dealer
      ? `<span class="inline-flex items-center justify-center w-6 h-6 rounded-full border border-black text-black" style="background-color: white;">D</span>`
      : "";

    const smallBlindMarkup = player.is_small_blind
      ? `<span class="inline-flex items-center justify-center w-6 h-6 rounded-full border border-black text-white" style="background-color: blue;">S</span>`
      : "";

    const bigBlindMarkup = player.is_big_blind
      ? `<span class="inline-flex items-center justify-center w-6 h-6 rounded-full border border-black text-black" style="background-color: yellow;">B</span>`
      : "";

    const foldMarkup = player.has_folded
      ? `<span class="inline-flex items-center justify-center w-6 h-6 rounded-full border border-black text-black" style="background-color: red;">X</span>`
      : "";

    if (player.is_next_to_play && gameStatus == "active") {
      li.classList.add("bg-emerald-800");
      nextMarkup = `<span class="items-center justify-center text-white text-sm">Your turn</span>`;
    } else {
      li.classList.add("bg-gray-700");
      nextMarkup = "";
    }

    li.innerHTML = `
      <span class="text-base">
        <span class="inline-block w-3 h-3 border border-white mr-1" style="background-color: ${player.avatar_color};"></span>
        ${player.username}
      </span>
      <span class="text-sm">Table chips: ${player.game_chips}</span>
      <span class="text-sm">Bet: ${player.current_bet}</span>
      <span class="text-sm font-bold my-2">
        ${dealerMarkup}
        ${smallBlindMarkup}
        ${bigBlindMarkup}
        ${foldMarkup}
      </span>
       ${nextMarkup}
    `;

    playerList.appendChild(li);
  });
}


/* -----------------------------------------------------------------------
 * Displays a temporary overlay message on the screen.
 * @param {string} message - The text to display.
 * @param {string} type - "error" or other.
 * @param {number} duration - Visibility duration in ms.
 * ----------------------------------------------------------------------*/
function showTemporaryMessage(message, type, duration = displayTime) {
  const overlay = document.getElementById('overlay');
  const overlayContent = document.getElementById('overlayContent');
  const messageElement = document.getElementById('overlayMessage');

  messageElement.textContent = message;

  overlay.classList.remove('hidden');
  overlay.classList.add('flex');

  const isError = type === "error";
  overlayContent.classList.toggle('bg-red-700', isError);
  overlayContent.classList.toggle('bg-gray-500', !isError);

  setTimeout(() => {
    overlay.classList.remove('flex');
    overlay.classList.add('hidden');
  }, duration);
}


function showTemporaryMessageTop(message, duration = displayTime) {
  const msg = document.getElementById('main-message');

  msg.textContent = message;
  msg.classList.remove('hidden');

  setTimeout(() => {
    msg.classList.add('hidden');
  }, duration);
}


/* -----------------------------------------------------------------------
 * Processes the incoming message queue sequentially.
 * ----------------------------------------------------------------------*/
function processQueue() {
  if (isProcessingQueue) return;
  isProcessingQueue = true;
  showNextMessage();
}


function showNextMessage() {
  if (messageQueue.length === 0) {
    isProcessingQueue = false;
    return;
  }

  const msg = messageQueue.shift();

  if (msg.startsWith("🏆")) {
    showTemporaryMessage(msg, "info", 2000);
  } else {
    showTemporaryMessageTop(msg, displayTime);
  }

  setTimeout(() => {
    const messagesList = document.getElementById("action-messages");

    const maxItems = 10;
    const existingItems = messagesList.querySelectorAll("li");
    if (existingItems.length >= maxItems) {
      messagesList.removeChild(existingItems[existingItems.length - 1]);
    }

    const li = document.createElement("li");
    li.innerText = msg;
    messagesList.insertBefore(li, messagesList.firstChild);

    showNextMessage();
  }, displayTime);
}


/* -----------------------------------------------------------------------
 * Initializes UI state and renders players/community cards on page load.
 * ----------------------------------------------------------------------*/
window.onload = function () {
  let playersData = GAME_CONFIG.playersData;
  renderPlayersList(playersData, "players-list");

  buttonsStateMachine(gameStatus, currentPhase, currentTurnUsername, username, isPlayer, playersData);

  loadInitialCommunityCards();

  const lastMessages = GAME_CONFIG.lastMessages;
  const messagesList = document.getElementById("action-messages");
  lastMessages.forEach(msg => {
    const li = document.createElement("li");
    li.innerText = msg;
    messagesList.insertBefore(li, messagesList.firstChild);
  });
};


/* -----------------------------------------------------------------------
 * Initial WebSocket Connection
 * ----------------------------------------------------------------------*/
connectWebSocket();
