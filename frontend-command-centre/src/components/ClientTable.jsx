import React from 'react';
import { ChevronDown, ChevronUp, Smartphone, HardDrive, Cpu, MemoryStick, Wifi, AlertCircle, CheckCircle2, XCircle, PowerOff } from 'lucide-react';

const getStatusClasses = (clientData) => {
  if (clientData.connected !== 'true') {
    return 'bg-slate-600/30 text-slate-400';
  }
  switch (clientData.client_state) {
    case 'running':
      return 'bg-green-500/20 text-green-400';
    case 'paused':
      return 'bg-yellow-500/20 text-yellow-400';
    case 'error':
      return 'bg-red-500/20 text-red-400';
    default:
      return 'bg-blue-500/20 text-blue-400';
  }
};

const getStatusIcon = (clientData) => {
  if (clientData.connected !== 'true') {
    return <PowerOff size={18} className="mr-2 text-slate-500" />;
  }
  switch (clientData.client_state) {
    case 'running':
      return <CheckCircle2 size={18} className="mr-2" />;
    case 'paused':
      return <AlertCircle size={18} className="mr-2 text-yellow-400" />;
    case 'error':
      return <XCircle size={18} className="mr-2" />;
    default:
      return <Wifi size={18} className="mr-2 text-blue-400" />;
  }
};

const ClientRow = ({ client }) => {
  const [expanded, setExpanded] = React.useState(false);

  return (
    <>
      <tr
        className="border-b border-slate-700 hover:bg-slate-700/50 transition-colors duration-150 cursor-pointer"
        onClick={() => setExpanded(!expanded)}
        tabIndex={0}
        aria-expanded={expanded}
        aria-label={`Client ${client.name || client.client_id}, status ${client.connected === 'true' ? client.client_state || 'connected' : 'disconnected'}. Click to ${expanded ? 'collapse' : 'expand'} details.`}
        onKeyDown={(e) => e.key === 'Enter' && setExpanded(!expanded)}
      >
        <td className="px-4 py-3 whitespace-nowrap">
          <div className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${getStatusClasses(client)}`}>
            {getStatusIcon(client)}
            {client.connected === 'true'
              ? (client.client_state ? client.client_state.charAt(0).toUpperCase() + client.client_state.slice(1) : 'Connected')
              : 'Disconnected'}
          </div>
        </td>
        <td className="px-4 py-3 font-medium text-purple-300 whitespace-nowrap">{client.client_id || client.name}</td>
        <td className="px-4 py-3 text-slate-400 whitespace-nowrap">{client.type || 'N/A'}</td>
        <td className="px-4 py-3 text-sky-400 whitespace-nowrap">{client.ip_address || 'N/A'}</td>
        <td className="px-4 py-3 text-slate-400 whitespace-nowrap">
          {client.disconnect_time
            ? new Date(client.disconnect_time).toLocaleString()
            : (client.connect_time ? new Date(client.connect_time).toLocaleString() : 'N/A')}
        </td>
        <td className="px-4 py-3 text-slate-300 whitespace-nowrap">
          {expanded ? <ChevronUp size={20} /> : <ChevronDown size={20} />}
        </td>
      </tr>
      {expanded && (
        <tr className="bg-slate-750">
          <td colSpan={6} className="p-0">
            <div className="p-4 grid grid-cols-1 md:grid-cols-3 gap-4 bg-slate-700/30">
              <div className="flex items-center space-x-2 p-3 bg-slate-800 rounded-md">
                <Cpu size={20} className="text-teal-400" />
                <div>
                  <p className="text-xs text-slate-400">CPU Usage</p>
                  <p className="text-sm font-semibold text-teal-300">{client.cpu_usage || 'N/A'}</p>
                </div>
              </div>
              <div className="flex items-center space-x-2 p-3 bg-slate-800 rounded-md">
                <MemoryStick size={20} className="text-orange-400" />
                <div>
                  <p className="text-xs text-slate-400">Memory</p>
                  <p className="text-sm font-semibold text-orange-300">{client.memory_usage || 'N/A'}</p>
                </div>
              </div>
              <div className="flex items-center space-x-2 p-3 bg-slate-800 rounded-md">
                <HardDrive size={20} className="text-indigo-400" />
                <div>
                  <p className="text-xs text-slate-400">Disk Space</p>
                  <p className="text-sm font-semibold text-indigo-300">{client.disk_usage || 'N/A'}</p>
                </div>
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
};

const ClientTable = ({ clients, isLoading, error, redisStatus }) => {
  if (isLoading) {
    return <p className="text-center text-slate-400 py-8">Loading client data...</p>;
  }

  if (error) {
    return <p className="text-center text-red-400 py-8">Error fetching data: {error}</p>;
  }

  const clientList = Object.values(clients);

  if (clientList.length === 0) {
    return (
      <div className="bg-slate-800 p-6 rounded-xl shadow-xl flex-grow flex flex-col min-h-[400px]">
        <h2 className="text-2xl font-semibold text-purple-400 mb-6">Registered Clients</h2>
        <p className="text-center text-slate-400 py-8">No client data available. Redis status: {redisStatus}</p>
      </div>
    );
  }

  return (
    <div className="bg-slate-800 p-6 rounded-xl shadow-xl flex-grow flex flex-col min-h-[400px]">
      <div className="flex justify-between items-center mb-6">
        <h2 className="text-2xl font-semibold text-purple-400">Registered Clients</h2>
        <span className={`text-xs px-2 py-1 rounded-full ${redisStatus === 'connected' ? 'bg-green-500/30 text-green-300' : 'bg-red-500/30 text-red-300'}`}>
          Redis: {redisStatus}
        </span>
      </div>
      <div className="overflow-x-auto flex-grow rounded-lg scrollbar-thin scrollbar-thumb-slate-600 scrollbar-track-slate-700">
        <table className="min-w-full divide-y divide-slate-700">
          <thead className="bg-slate-700/50 sticky top-0 z-10">
            <tr>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-slate-300 uppercase tracking-wider">Status</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-slate-300 uppercase tracking-wider">Name</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-slate-300 uppercase tracking-wider">Type</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-slate-300 uppercase tracking-wider">IP Address</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-slate-300 uppercase tracking-wider">Last Seen</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-slate-300 uppercase tracking-wider"></th>
            </tr>
          </thead>
          <tbody className="bg-slate-800 divide-y divide-slate-700">
            {clientList.map((client, index) => {
              if (!client || typeof client.client_id === 'undefined') {
                console.warn('ClientTable: Rendering ClientRow with potentially problematic client data or client_id:', client, 'at index:', index);
              }
              const key = client && client.client_id ? client.client_id : `client-row-${index}`;
              return <ClientRow key={key} client={client} />;
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default ClientTable; 