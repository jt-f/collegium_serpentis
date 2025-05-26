import React from 'react';
import { ShieldCheck, SatelliteDish, Bot, MessageSquare, Settings, LogOut } from 'lucide-react';

const SidebarIcon = ({ icon, text = 'tooltip 📝' }) => (
    <div className="sidebar-icon group">
        {icon}
        <span className="sidebar-tooltip group-hover:scale-100">
            {text}
        </span>
    </div>
);

const Layout = ({ children }) => {
    return (
        <div className="flex h-screen bg-slate-800">
            {/* Sidebar */}
            <div className="fixed top-0 left-0 h-screen w-20 m-0 flex flex-col bg-slate-900 text-white shadow-lg">
                <SidebarIcon icon={<ShieldCheck size="28" />} text="Dashboard" />
                <SidebarIcon icon={<SatelliteDish size="28" />} text="Clients" />
                <SidebarIcon icon={<Bot size="28" />} text="Automation" />
                <SidebarIcon icon={<MessageSquare size="28" />} text="Messages" />

                <div className="mt-auto mb-4">
                    <SidebarIcon icon={<Settings size="28" />} text="Settings" />
                    <SidebarIcon icon={<LogOut size="28" />} text="Logout" />
                </div>
            </div>

            {/* Main Content */}
            <main className="flex-grow ml-20 transition-all duration-300 ease-in-out">
                {children}
            </main>
        </div>
    );
};

export default Layout; 