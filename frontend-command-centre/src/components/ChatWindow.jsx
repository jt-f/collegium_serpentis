import React, { useState, useRef, useEffect } from 'react';
import { Send, CornerDownLeft, UserCircle, Bot, Clock, CheckCircle, AlertCircle, ChevronDown } from 'lucide-react';

const ChatMessage = ({ message, clients = {} }) => {
  const isOwnMessage = message.isOwnMessage === true;
  const isPending = !message.acknowledged && !message.error && isOwnMessage;
  const hasError = message.error;
  const isResponse = !!message.in_response_to_message_id;
  const senderRole = message.sender_role || 'unknown';

  // Get target client name for display with fallback to "All"
  const getTargetDisplayName = (targetId) => {
    if (!targetId) return "All";
    const targetClient = clients[targetId];
    return targetClient?.client_name || targetClient?.client_id || targetId;
  };

  // Get sender display name for incoming messages
  const getSenderDisplayName = (senderId) => {
    const senderClient = clients[senderId];
    return senderClient?.client_name || senderClient?.client_id || senderId;
  };

  // Determine target info for display
  const targetDisplay = getTargetDisplayName(message.target_id);
  const isBroadcast = !message.target_id || message.target_id === "";

  // Get display icon based on sender role
  const getSenderIcon = () => {
    if (isOwnMessage) {
      return <UserCircle size={20} className="flex-shrink-0 opacity-80" />;
    } else if (senderRole === 'worker') {
      return <Bot size={20} className="flex-shrink-0 text-sky-400" />;
    } else {
      return <UserCircle size={20} className="flex-shrink-0 text-purple-400" />;
    }
  };

  return (
    <div className={`flex ${isOwnMessage ? 'justify-end' : 'justify-start'} mb-3`}>
      <div
        className={`p-3 rounded-lg max-w-[70%] flex items-start space-x-2 shadow transition-opacity duration-300 ${isOwnMessage
          ? `bg-purple-600 text-white rounded-br-none ${isPending ? 'opacity-50' : hasError ? 'bg-red-600' : ''}`
          : senderRole === 'worker'
            ? 'bg-slate-700 text-slate-100 rounded-bl-none border-l-2 border-sky-400'
            : 'bg-slate-700 text-slate-100 rounded-bl-none border-l-2 border-purple-400'
          }`}
      >
        {getSenderIcon()}
        <div className="flex-1 min-w-0">
          {/* Sender info for incoming messages */}
          {!isOwnMessage && (
            <div className="text-xs mb-1 font-medium flex items-center space-x-2">
              <span className={senderRole === 'worker' ? 'text-sky-300' : 'text-purple-300'}>
                {getSenderDisplayName(message.sender)}
              </span>
              <span className="text-slate-400 text-xs">
                ({senderRole})
              </span>
            </div>
          )}

          {/* Response indicator */}
          {isResponse && (
            <div className="text-xs text-yellow-300 mb-1 italic">
              ↳ Responding to message {message.in_response_to_message_id?.substring(0, 8)}...
            </div>
          )}

          {/* Target info - show for all messages */}
          <div className="text-xs mb-1 font-medium flex items-center space-x-1">
            <span className={isOwnMessage ? "text-purple-200" : "text-slate-400"}>
              To:
            </span>
            <span className={
              isBroadcast
                ? (isOwnMessage ? "text-purple-200" : "text-yellow-300")
                : (isOwnMessage ? "text-purple-200" : "text-green-300")
            }>
              {targetDisplay}
              {isBroadcast && (
                <span className="ml-1 text-xs opacity-70">(Broadcast)</span>
              )}
            </span>
          </div>

          {/* Message content */}
          <p className="text-sm break-words">{message.text || message.message}</p>

          {/* Timestamp and status info */}
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
                {message.message_id && (
                  <span className="text-xs opacity-40 ml-2">
                    ID: {message.message_id.substring(0, 8)}...
                  </span>
                )}
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
    const options = [{ id: 'all', name: 'All Workers (Broadcast)', shortName: 'All' }];

    Object.values(clients).forEach(client => {
      // Only include connected Python clients (workers), exclude other frontends
      if (client.connected === 'true' && client.client_role === 'worker') {
        const displayName = client.client_name || client.client_id;
        options.push({
          id: client.client_id,
          name: `${displayName}`,
          shortName: displayName,
          fullName: `${displayName} (${client.client_id})`
        });
      }
    });

    return options;
  };

  const targetOptions = getTargetOptions();
  const selectedTarget = targetOptions.find(option => option.id === selectedTargetId) || targetOptions[0];

  // Get user-friendly target name for placeholders
  const getTargetDisplayForPlaceholder = () => {
    if (!selectedTargetId) return "all workers";
    const target = targetOptions.find(option => option.id === selectedTargetId);
    return target?.shortName || selectedTargetId;
  };

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
                  `Message to ${getTargetDisplayForPlaceholder()}...`
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