import React, { useState, useRef, useEffect } from 'react';
import { Send, CornerDownLeft, UserCircle, Bot, Clock, CheckCircle, AlertCircle, ChevronDown } from 'lucide-react';

const ChatMessage = ({ message, clients = {} }) => {
  const isOwnMessage = message.isOwnMessage === true;
  const isPending = !message.acknowledged && !message.error && isOwnMessage;
  const hasError = message.error;

  // Get target client name for display
  const getTargetDisplayName = (targetId) => {
    if (!targetId) return null;
    const targetClient = clients[targetId];
    return targetClient?.client_name || targetClient?.client_id || targetId;
  };

  return (
    <div className={`flex ${isOwnMessage ? 'justify-end' : 'justify-start'} mb-3`}>
      <div
        className={`p-3 rounded-lg max-w-[70%] flex items-start space-x-2 shadow transition-opacity duration-300 ${isOwnMessage
          ? `bg-purple-600 text-white rounded-br-none ${isPending ? 'opacity-50' : hasError ? 'bg-red-600' : ''}`
          : 'bg-slate-700 text-slate-100 rounded-bl-none'
          }`}
      >
        {isOwnMessage ? (
          <UserCircle size={20} className="flex-shrink-0 opacity-80" />
        ) : (
          <Bot size={20} className="flex-shrink-0 text-sky-400" />
        )}
        <div className="flex-1 min-w-0">
          {!isOwnMessage && (
            <div className="text-xs text-sky-300 mb-1 font-medium">
              {message.sender}
            </div>
          )}
          {message.target_id && !isOwnMessage && (
            <div className="text-xs text-yellow-300 mb-1 font-medium">
              To: {getTargetDisplayName(message.target_id)}
            </div>
          )}
          <p className="text-sm break-words">{message.text || message.message}</p>
          {message.targetId && isOwnMessage && (
            <div className="text-xs text-purple-300 mb-1">
              To: {getTargetDisplayName(message.targetId)}
            </div>
          )}
          <div className="flex items-center mt-1 space-x-1">
            {isOwnMessage && isPending ? (
              <>
                <Clock size={12} className="opacity-60" />
                <span className="text-xs opacity-60">Sending...</span>
              </>
            ) : isOwnMessage && hasError ? (
              <>
                <AlertCircle size={12} className="text-red-300" />
                <span className="text-xs text-red-300">Failed to send</span>
              </>
            ) : message.timestamp ? (
              <>
                {isOwnMessage && <CheckCircle size={12} className="opacity-60" />}
                <span className="text-xs opacity-60">
                  {new Date(message.timestamp).toLocaleTimeString()}
                </span>
              </>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
};

const ChatWindow = ({ messages = [], onSendMessage, wsStatus, isRegistered = false, clients = {}, selectedTargetId, onTargetChange }) => {
  const [inputText, setInputText] = useState('');
  const messagesEndRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(scrollToBottom, [messages]);

  const handleSendMessage = (e) => {
    e.preventDefault();
    if (inputText.trim() === '') return;

    // Call the parent's send message function with target
    if (onSendMessage) {
      onSendMessage(inputText.trim(), selectedTargetId);
    }

    setInputText('');
  };

  // Get available targets for dropdown (only Python clients/workers)
  const getTargetOptions = () => {
    const options = [{ id: 'all', name: 'All (Broadcast)' }];

    Object.values(clients).forEach(client => {
      // Only include connected Python clients (workers), exclude other frontends
      if (client.connected === 'true' && client.client_role === 'worker') {
        options.push({
          id: client.client_id,
          name: `${client.client_name || client.client_id} (Python Client)`
        });
      }
    });

    return options;
  };

  const targetOptions = getTargetOptions();
  const selectedTarget = targetOptions.find(option => option.id === selectedTargetId) || targetOptions[0];

  const isDisconnected = wsStatus !== 'connected';
  const isNotReady = isDisconnected || !isRegistered;

  return (
    <div className="bg-slate-800 p-6 rounded-xl shadow-xl flex flex-col h-full max-h-[calc(100vh-3rem)] lg:max-h-full">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-2xl font-semibold text-purple-400">Event Log / Chat</h2>
        <div className={`px-2 py-1 rounded text-xs ${wsStatus === 'connected' && isRegistered ? 'bg-green-600 text-white' :
          wsStatus === 'connected' && !isRegistered ? 'bg-yellow-600 text-white' :
            wsStatus === 'connecting' ? 'bg-yellow-600 text-white' :
              'bg-red-600 text-white'
          }`}>
          {wsStatus === 'connected' && !isRegistered ? 'registering' : wsStatus}
        </div>
      </div>

      <div className="flex-grow overflow-y-auto mb-4 pr-2 scrollbar-thin scrollbar-thumb-slate-600 scrollbar-track-slate-700/50">
        {messages.map((msg) => (
          <ChatMessage key={msg.id || msg.tempId} message={msg} clients={clients} />
        ))}
        <div ref={messagesEndRef} />
      </div>

      <div className="mt-auto space-y-2">
        {/* Target Selection */}
        <div className="flex items-center space-x-2">
          <span className="text-sm text-slate-300 whitespace-nowrap">Send to:</span>
          <div className="relative flex-grow">
            <select
              value={selectedTargetId || 'all'}
              onChange={(e) => onTargetChange && onTargetChange(e.target.value === 'all' ? null : e.target.value)}
              disabled={isNotReady}
              className={`w-full bg-slate-600 text-slate-100 p-2 rounded-md focus:ring-2 focus:ring-purple-500 focus:outline-none text-sm appearance-none cursor-pointer transition-opacity ${isNotReady ? 'opacity-50 cursor-not-allowed' : ''
                }`}
              aria-label="Select message target"
            >
              {targetOptions.map(option => (
                <option key={option.id} value={option.id}>
                  {option.name}
                </option>
              ))}
            </select>
            <ChevronDown size={16} className="absolute right-2 top-1/2 transform -translate-y-1/2 text-slate-400 pointer-events-none" />
          </div>
        </div>

        {/* Message Input */}
        <form onSubmit={handleSendMessage} className="flex items-center space-x-2 bg-slate-700 p-3 rounded-lg">
          <input
            type="text"
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            placeholder={
              isDisconnected ? "WebSocket disconnected..." :
                !isRegistered ? "Registering with server..." :
                  selectedTargetId ? `Direct message to ${selectedTarget?.name}...` : "Type a message to all clients..."
            }
            disabled={isNotReady}
            className={`flex-grow bg-slate-600 text-slate-100 placeholder-slate-400 p-3 rounded-md focus:ring-2 focus:ring-purple-500 focus:outline-none text-sm transition-opacity ${isNotReady ? 'opacity-50 cursor-not-allowed' : ''
              }`}
            aria-label="Chat message input"
          />
          <button
            type="submit"
            disabled={isNotReady || inputText.trim() === ''}
            className={`p-3 bg-purple-600 text-white rounded-md hover:bg-purple-700 focus:outline-none focus:ring-2 focus:ring-purple-500 focus:ring-offset-2 focus:ring-offset-slate-700 transition-colors duration-150 ${(isNotReady || inputText.trim() === '') ? 'opacity-50 cursor-not-allowed' : ''
              }`}
            aria-label="Send message"
          >
            <Send size={20} />
          </button>
        </form>
      </div>

      {isNotReady && (
        <div className="mt-2 text-xs text-center">
          {isDisconnected ? (
            <span className="text-red-400">Connection lost. Messages cannot be sent until reconnected.</span>
          ) : !isRegistered ? (
            <span className="text-yellow-400">Registering with server. Please wait...</span>
          ) : null}
        </div>
      )}
    </div>
  );
};

export default ChatWindow; 