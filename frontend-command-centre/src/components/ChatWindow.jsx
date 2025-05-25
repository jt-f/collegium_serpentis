import React, { useState, useRef, useEffect } from 'react';
import { Send, CornerDownLeft, UserCircle, Bot } from 'lucide-react';

const ChatMessage = ({ message }) => {
  const isOperator = message.sender === 'Operator';
  return (
    <div className={`flex ${isOperator ? 'justify-end' : 'justify-start'} mb-3`}>
      <div 
        className={`p-3 rounded-lg max-w-[70%] flex items-start space-x-2 shadow ${isOperator 
            ? 'bg-purple-600 text-white rounded-br-none' 
            : 'bg-slate-700 text-slate-100 rounded-bl-none'}`}
        >
        {isOperator ? (
          <UserCircle size={20} className="flex-shrink-0 opacity-80" />
        ) : (
          <Bot size={20} className="flex-shrink-0 text-sky-400" />
        )}
        <p className="text-sm">{message.text}</p>
      </div>
    </div>
  );
};

const ChatWindow = ({ messages: initialMessages }) => {
  const [messages, setMessages] = useState(initialMessages || []);
  const [inputText, setInputText] = useState('');
  const messagesEndRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(scrollToBottom, [messages]);

  const handleSendMessage = (e) => {
    e.preventDefault();
    if (inputText.trim() === '') return;
    const newMessage = {
      id: messages.length + 1,
      sender: 'Operator', // Assuming messages from input are from the operator
      text: inputText.trim(),
    };
    setMessages([...messages, newMessage]);
    setInputText('');
    // Here you would also send the message to the backend via WebSocket
  };

  return (
    <div className="bg-slate-800 p-6 rounded-xl shadow-xl flex flex-col h-full max-h-[calc(100vh-3rem)] lg:max-h-full">
      <h2 className="text-2xl font-semibold text-purple-400 mb-4">Event Log / Chat</h2>
      
      <div className="flex-grow overflow-y-auto mb-4 pr-2 scrollbar-thin scrollbar-thumb-slate-600 scrollbar-track-slate-700/50">
        {messages.map((msg) => (
          <ChatMessage key={msg.id} message={msg} />
        ))}
        <div ref={messagesEndRef} />
      </div>

      <form onSubmit={handleSendMessage} className="mt-auto flex items-center space-x-2 bg-slate-700 p-3 rounded-lg">
        <input 
          type="text"
          value={inputText}
          onChange={(e) => setInputText(e.target.value)}
          placeholder="Type a message to clients or log an event..."
          className="flex-grow bg-slate-600 text-slate-100 placeholder-slate-400 p-3 rounded-md focus:ring-2 focus:ring-purple-500 focus:outline-none text-sm"
          aria-label="Chat message input"
        />
        <button 
          type="submit" 
          className="p-3 bg-purple-600 text-white rounded-md hover:bg-purple-700 focus:outline-none focus:ring-2 focus:ring-purple-500 focus:ring-offset-2 focus:ring-offset-slate-700 transition-colors duration-150"
          aria-label="Send message"
          >
          <Send size={20} />
        </button>
      </form>
    </div>
  );
};

export default ChatWindow; 